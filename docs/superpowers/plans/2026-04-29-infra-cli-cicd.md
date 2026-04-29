# infra CLI CI/CD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the manual `git pull && go build` update flow with a tag-triggered GitHub Actions pipeline that publishes signed binaries to a LAN mirror on ct-mgmt, so any host can self-update without a Go toolchain or repo checkout.

**Architecture:** GitHub Actions builds linux/amd64 + linux/arm64 on `v*` tag push and attaches binaries + sha256 files to a GitHub Release. A systemd timer on ct-mgmt polls Releases via a fine-grained PAT, verifies, and re-publishes to a LAN-served Caddy directory. The CLI reads `https://infra-bin.lan/manifest.json` for both `infra update` (active) and a passive 24h staleness check.

**Tech Stack:** Go 1.26 + cobra (existing CLI), GitHub Actions (with `softprops/action-gh-release@v2`), Caddy (Docker, on ct-mgmt), systemd timers + `curl`/`jq` (mirror), Pi-hole local DNS records (ct-dns).

**Spec:** `docs/superpowers/specs/2026-04-29-infra-cli-cicd-design.md`

---

## File Structure

**Create (Go):**
- `cli/internal/manifest/manifest.go` — `Manifest`/`Binary` types + `Fetch(ctx, url)` HTTP client.
- `cli/internal/manifest/manifest_test.go`
- `cli/internal/updatecheck/cache.go` — read/write `~/.cache/infra/update-check.json`, TTL helper.
- `cli/internal/updatecheck/cache_test.go`
- `cli/internal/updatecheck/check.go` — passive check orchestrator (PreRun fetch + PostRun footer).
- `cli/internal/updatecheck/check_test.go`

**Create (CI):**
- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`

**Create (mirror service, deployed manually to ct-mgmt):**
- `stacks/ct-mgmt/infra-mirror/sync.sh`
- `stacks/ct-mgmt/infra-mirror/install.sh`
- `stacks/ct-mgmt/infra-mirror/infra-mirror.service`
- `stacks/ct-mgmt/infra-mirror/infra-mirror.timer`
- `stacks/ct-mgmt/infra-mirror/README.md`

**Modify:**
- `cli/internal/cmd/update.go` — refactor: extract existing logic into `runFromSource`; add new `runFromMirror` as default path; new flags `--from-source`, `--mirror`.
- `cli/internal/cmd/root.go` — wire updatecheck into root cobra `PersistentPreRun` and `PersistentPostRun`.
- `cli/Makefile` — remove `deploy` target.
- `stacks/ct-mgmt/Caddyfile` — add `infra-bin.lan` site block.
- `stacks/ct-mgmt/docker-compose.yml` — bind-mount host `/var/www/infra-bin` into caddy container as `/srv/infra-bin:ro`.
- `CLAUDE.md` — add infra-mirror service note under ct-mgmt section + DNS note.

**Manual ops (no commit; documented in mirror README):**
- ct-mgmt: install systemd unit + timer, mkdir paths, store PAT, redeploy caddy compose with new mount.
- ct-dns: add Pi-hole local DNS record `infra-bin.lan → 192.168.3.12`.
- GitHub: create fine-grained PAT (Contents:Read, Metadata:Read), scoped to `psychonaut0/infra`.

**Each task is a single commit.** Phase G's manual steps live in the README and are executed by hand.

---

## Phase A — Shared Manifest Types

### Task 1: `manifest` package — types + parser

**Files:**
- Create: `cli/internal/manifest/manifest.go`
- Create: `cli/internal/manifest/manifest_test.go`

- [ ] **Step 1: Write the failing test**

```go
// cli/internal/manifest/manifest_test.go
package manifest

import (
	"strings"
	"testing"
)

func TestParse_HappyPath(t *testing.T) {
	in := `{
		"version": "v0.4.0",
		"commit": "bd5e181",
		"published_at": "2026-04-29T12:34:56Z",
		"binaries": {
			"linux/amd64": {
				"url": "https://infra-bin.lan/linux/amd64/infra",
				"sha256": "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8"
			},
			"linux/arm64": {
				"url": "https://infra-bin.lan/linux/arm64/infra",
				"sha256": "ef797c8118f02dfb649607dd5d3f8c7623048c9c063d532cc95c5ed7a898a64f"
			}
		}
	}`
	m, err := Parse(strings.NewReader(in))
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	if m.Version != "v0.4.0" {
		t.Errorf("Version = %q, want v0.4.0", m.Version)
	}
	b, ok := m.Binaries["linux/amd64"]
	if !ok {
		t.Fatal("linux/amd64 missing")
	}
	if b.SHA256 != "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8" {
		t.Errorf("sha mismatch: %s", b.SHA256)
	}
}

func TestParse_MalformedJSON(t *testing.T) {
	_, err := Parse(strings.NewReader("not json"))
	if err == nil {
		t.Fatal("expected error for malformed JSON")
	}
}

func TestForArch_Missing(t *testing.T) {
	m := &Manifest{Binaries: map[string]Binary{
		"linux/amd64": {URL: "x", SHA256: "y"},
	}}
	_, err := m.ForArch("linux/arm64")
	if err == nil {
		t.Fatal("expected error for missing arch")
	}
}

func TestForArch_Hit(t *testing.T) {
	m := &Manifest{Binaries: map[string]Binary{
		"linux/amd64": {URL: "u", SHA256: "s"},
	}}
	b, err := m.ForArch("linux/amd64")
	if err != nil {
		t.Fatalf("ForArch: %v", err)
	}
	if b.URL != "u" {
		t.Errorf("URL = %q", b.URL)
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

```sh
cd cli && go test ./internal/manifest/...
```
Expected: FAIL — `package not found` or `undefined: Parse`.

- [ ] **Step 3: Write minimal implementation**

```go
// cli/internal/manifest/manifest.go

// Package manifest defines the schema and a parser for the release mirror's
// manifest.json, which advertises the latest published infra binaries.
package manifest

import (
	"encoding/json"
	"fmt"
	"io"
)

// Binary describes one published artifact (one OS/arch).
type Binary struct {
	URL    string `json:"url"`
	SHA256 string `json:"sha256"`
}

// Manifest is the top-level document served at <mirror>/manifest.json.
type Manifest struct {
	Version     string            `json:"version"`
	Commit      string            `json:"commit"`
	PublishedAt string            `json:"published_at"`
	Binaries    map[string]Binary `json:"binaries"`
}

// Parse decodes a Manifest from r.
func Parse(r io.Reader) (*Manifest, error) {
	var m Manifest
	dec := json.NewDecoder(r)
	dec.DisallowUnknownFields()
	if err := dec.Decode(&m); err != nil {
		return nil, fmt.Errorf("decode manifest: %w", err)
	}
	return &m, nil
}

// ForArch returns the binary for "<goos>/<goarch>" or an error if missing.
func (m *Manifest) ForArch(goosGoarch string) (Binary, error) {
	b, ok := m.Binaries[goosGoarch]
	if !ok {
		return Binary{}, fmt.Errorf("no binary for %s in manifest", goosGoarch)
	}
	return b, nil
}
```

- [ ] **Step 4: Run tests to verify they pass**

```sh
cd cli && go test ./internal/manifest/...
```
Expected: PASS, all 4 tests.

- [ ] **Step 5: Commit**

```sh
git add cli/internal/manifest/
git commit -m "cli/manifest: add release manifest types and parser"
```

---

### Task 2: `manifest.Fetch` — HTTP client

**Files:**
- Modify: `cli/internal/manifest/manifest.go`
- Modify: `cli/internal/manifest/manifest_test.go`

- [ ] **Step 1: Write the failing test**

Append to `manifest_test.go`:

```go
import (
	"context"
	"net/http"
	"net/http/httptest"
	"time"
)

func TestFetch_Success(t *testing.T) {
	body := `{"version":"v0.4.0","commit":"abc","published_at":"2026-04-29T00:00:00Z","binaries":{"linux/amd64":{"url":"u","sha256":"s"}}}`
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(body))
	}))
	defer srv.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	m, err := Fetch(ctx, srv.URL)
	if err != nil {
		t.Fatalf("Fetch: %v", err)
	}
	if m.Version != "v0.4.0" {
		t.Errorf("Version = %q", m.Version)
	}
}

func TestFetch_Non200(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "boom", http.StatusInternalServerError)
	}))
	defer srv.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if _, err := Fetch(ctx, srv.URL); err == nil {
		t.Fatal("expected error for 500")
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

```sh
cd cli && go test ./internal/manifest/...
```
Expected: FAIL — `undefined: Fetch`.

- [ ] **Step 3: Write minimal implementation**

Append to `manifest.go`:

```go
import (
	"net/http"
	"context"
)

// Fetch GETs a manifest from url. Returns an error on non-200 responses,
// transport errors, or context cancellation.
func Fetch(ctx context.Context, url string) (*Manifest, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, fmt.Errorf("manifest request: %w", err)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("manifest fetch: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("manifest fetch: HTTP %d", resp.StatusCode)
	}
	return Parse(resp.Body)
}
```

(Note: merge the new imports — `context`, `net/http` — into the existing import block. Keep `encoding/json`, `fmt`, `io`.)

- [ ] **Step 4: Run tests to verify they pass**

```sh
cd cli && go test ./internal/manifest/...
```
Expected: PASS, all 6 tests.

- [ ] **Step 5: Commit**

```sh
git add cli/internal/manifest/
git commit -m "cli/manifest: add HTTP Fetch helper"
```

---

### Task 3: Semver compare helper

**Files:**
- Modify: `cli/internal/manifest/manifest.go`
- Modify: `cli/internal/manifest/manifest_test.go`

This avoids pulling a full semver library; we only need a coarse "is A newer than B" predicate. `dev` is treated as "older than anything tagged" so dev builds always see "update available".

- [ ] **Step 1: Write the failing test**

Append to `manifest_test.go`:

```go
func TestNewer(t *testing.T) {
	cases := []struct {
		latest, current string
		want            bool
	}{
		{"v0.4.0", "v0.3.0", true},
		{"v0.4.0", "v0.4.0", false},
		{"v0.3.0", "v0.4.0", false},
		{"v0.4.10", "v0.4.2", true},
		{"v1.0.0", "v0.99.99", true},
		{"v0.4.0", "dev", true},
		{"v0.4.0", "v0.4.0-2-gabc1234-dirty", true},
		{"v0.4.0", "", true},
	}
	for _, c := range cases {
		got := Newer(c.latest, c.current)
		if got != c.want {
			t.Errorf("Newer(%q, %q) = %v, want %v", c.latest, c.current, got, c.want)
		}
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

```sh
cd cli && go test ./internal/manifest/...
```
Expected: FAIL — `undefined: Newer`.

- [ ] **Step 3: Write minimal implementation**

Append to `manifest.go`:

```go
import (
	"strconv"
	"strings"
)

// Newer reports whether latest is strictly newer than current.
// Both arguments are expected in `vMAJOR.MINOR.PATCH` form (additional
// suffixes like `-2-gabc-dirty` are tolerated and treated as the base tag's
// pre-release). The literal "dev" and the empty string are treated as older
// than any tagged version.
func Newer(latest, current string) bool {
	if current == "" || current == "dev" {
		return latest != "" && latest != "dev"
	}
	la := parseSemver(latest)
	cu := parseSemver(current)
	for i := 0; i < 3; i++ {
		if la[i] != cu[i] {
			return la[i] > cu[i]
		}
	}
	// Equal core. If current has a -N-gSHA suffix it's a dev build past the
	// tag, so it's already newer than the tag itself: latest is NOT newer.
	return false
}

func parseSemver(v string) [3]int {
	v = strings.TrimPrefix(v, "v")
	// Strip any suffix after the third dot-separated number.
	core := v
	if i := strings.IndexAny(v, "-+"); i >= 0 {
		core = v[:i]
	}
	parts := strings.SplitN(core, ".", 3)
	out := [3]int{}
	for i := 0; i < len(parts) && i < 3; i++ {
		n, _ := strconv.Atoi(parts[i])
		out[i] = n
	}
	return out
}
```

(Add `strconv` and `strings` to the existing import block.)

- [ ] **Step 4: Run tests to verify they pass**

```sh
cd cli && go test ./internal/manifest/...
```
Expected: PASS, all 7 tests.

- [ ] **Step 5: Commit**

```sh
git add cli/internal/manifest/
git commit -m "cli/manifest: add Newer semver-compare helper"
```

---

## Phase B — `infra update` Rework

### Task 4: Extract existing logic into `runFromSource`

Pure refactor — same behavior, isolated in a private function so a new code path can sit beside it.

**Files:**
- Modify: `cli/internal/cmd/update.go`

- [ ] **Step 1: Inspect current behavior**

```sh
cd /home/psy/Documents/personal/infra
git log -1 --format=%H -- cli/internal/cmd/update.go
go test ./cli/...
```
Note the current test output (none for update.go specifically); refactor must not change behavior.

- [ ] **Step 2: Refactor `RunE` to delegate to `runFromSource`**

In `cli/internal/cmd/update.go`, replace the `RunE` block. Keep the flag declarations and the helper functions (`runGit`, `revParse`, `currentBranch`, `countAheadBehind`, `short`, `installPath`, `buildLDFlags`, `gitDescribe`, `gitShortSHA`) untouched. The new `RunE` is:

```go
RunE: func(cmd *cobra.Command, _ []string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	return runFromSource(ctx, cmd, runFromSourceOpts{
		Check: check,
		Yes:   yes,
		Ref:   ref,
	})
},
```

Add at the bottom of the file:

```go
type runFromSourceOpts struct {
	Check bool
	Yes   bool
	Ref   string
}

// runFromSource implements the historical "git pull && go build" update flow.
// Retained as a fallback for hosts with a repo checkout when the mirror is
// unreachable or for development.
func runFromSource(ctx context.Context, _ *cobra.Command, opts runFromSourceOpts) error {
	root, err := repo.Locate()
	if err != nil {
		return err
	}

	if err := runGit(ctx, root, "fetch", "--quiet"); err != nil {
		return fmt.Errorf("git fetch: %w", err)
	}

	branch := opts.Ref
	if branch == "" {
		branch = currentBranch(ctx, root)
	}
	remoteRef := "origin/" + branch

	local := revParse(ctx, root, "HEAD")
	remote := revParse(ctx, root, remoteRef)
	if local == "" || remote == "" {
		return fmt.Errorf("could not resolve git refs")
	}
	if local == remote {
		fmt.Printf("Up-to-date: %s is at %s\n", branch, short(local))
		return nil
	}

	ahead, behind := countAheadBehind(ctx, root, branch)
	fmt.Printf("Current: %s\nOrigin:  %s (behind %d, ahead %d)\n",
		short(local), short(remote), behind, ahead)
	if opts.Check {
		return nil
	}

	if !opts.Yes {
		ok, err := ui.Confirm("Pull and rebuild?")
		if err != nil {
			return err
		}
		if !ok {
			return nil
		}
	}

	if err := runGit(ctx, root, "pull", "--ff-only"); err != nil {
		return fmt.Errorf("git pull: %w", err)
	}

	installTarget, err := installPath()
	if err != nil {
		return err
	}
	tmp := installTarget + ".new"
	cliDir := filepath.Join(root, "cli")
	build := exec.CommandContext(ctx, "go", "build",
		"-ldflags", buildLDFlags(root),
		"-o", tmp,
		"./cmd/infra")
	build.Dir = cliDir
	build.Stdout = os.Stdout
	build.Stderr = os.Stderr
	if err := build.Run(); err != nil {
		_ = os.Remove(tmp)
		return fmt.Errorf("go build: %w", err)
	}
	verify := exec.CommandContext(ctx, tmp, "version")
	if out, err := verify.CombinedOutput(); err != nil {
		_ = os.Remove(tmp)
		return fmt.Errorf("verify new binary failed: %w\n%s", err, out)
	}
	if err := os.Rename(tmp, installTarget); err != nil {
		return fmt.Errorf("install: %w", err)
	}
	fmt.Printf("Updated %s\n", installTarget)
	return nil
}
```

Delete the old inline body that lived inside `RunE` (everything that's now in `runFromSource`).

- [ ] **Step 3: Build to verify nothing broke**

```sh
cd cli && go build ./... && go test ./...
```
Expected: build success; tests pass.

- [ ] **Step 4: Smoke-test the binary**

```sh
cd cli && go run ./cmd/infra update --check
```
Expected: prints `Up-to-date: ...` or current/origin status (same output as before).

- [ ] **Step 5: Commit**

```sh
git add cli/internal/cmd/update.go
git commit -m "cli/update: extract source-build flow into runFromSource (no behavior change)"
```

---

### Task 5: Add `runFromMirror` (default path)

**Files:**
- Modify: `cli/internal/cmd/update.go`
- Create: `cli/internal/cmd/update_test.go`

- [ ] **Step 1: Write the failing test**

```go
// cli/internal/cmd/update_test.go
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
	// Pre-existing binary so atomic rename has something to replace.
	if err := os.WriteFile(dest, []byte("old"), 0755); err != nil {
		t.Fatal(err)
	}

	var out bytes.Buffer
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	err := runFromMirror(ctx, runFromMirrorOpts{
		MirrorURL:    srv.URL + "/manifest.json",
		CurrentVer:   "v0.4.0",
		InstallPath:  dest,
		Yes:          true,
		Check:        false,
		Out:          &out,
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
```

Add `"time"` to the imports.

- [ ] **Step 2: Run tests to verify they fail**

```sh
cd cli && go test ./internal/cmd/... -run TestRunFromMirror
```
Expected: FAIL — `undefined: runFromMirror`.

- [ ] **Step 3: Write minimal implementation**

Append to `cli/internal/cmd/update.go`:

```go
import (
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net/http"
	"runtime"

	"github.com/psychonaut0/infra/cli/internal/manifest"
)

type runFromMirrorOpts struct {
	MirrorURL   string
	CurrentVer  string
	InstallPath string
	Check       bool
	Yes         bool
	Out         io.Writer
}

// runFromMirror implements the default update flow: pull the manifest from the
// LAN mirror, verify sha256, atomic-rename over the running binary.
func runFromMirror(ctx context.Context, opts runFromMirrorOpts) error {
	if opts.Out == nil {
		opts.Out = os.Stdout
	}
	m, err := manifest.Fetch(ctx, opts.MirrorURL)
	if err != nil {
		return fmt.Errorf("mirror at %s unreachable: %w (use --from-source if you have a repo checkout)", opts.MirrorURL, err)
	}
	arch := runtime.GOOS + "/" + runtime.GOARCH
	bin, err := m.ForArch(arch)
	if err != nil {
		return err
	}
	fmt.Fprintf(opts.Out, "Current: %s\nMirror:  %s\n", opts.CurrentVer, m.Version)
	if !manifest.Newer(m.Version, opts.CurrentVer) {
		fmt.Fprintf(opts.Out, "Already on latest (%s).\n", m.Version)
		return nil
	}
	if opts.Check {
		return nil
	}
	if !opts.Yes {
		ok, err := ui.Confirm(fmt.Sprintf("Update %s → %s?", opts.CurrentVer, m.Version))
		if err != nil {
			return err
		}
		if !ok {
			return nil
		}
	}

	tmp := opts.InstallPath + ".new"
	if err := download(ctx, bin.URL, tmp); err != nil {
		return err
	}
	defer os.Remove(tmp) // no-op on success after rename

	got, err := sha256File(tmp)
	if err != nil {
		return err
	}
	if got != bin.SHA256 {
		_ = os.Remove(tmp)
		return fmt.Errorf("downloaded binary failed checksum verification (got %s, want %s); existing binary unchanged", got, bin.SHA256)
	}
	if err := os.Chmod(tmp, 0755); err != nil {
		return fmt.Errorf("chmod: %w", err)
	}
	if err := os.Rename(tmp, opts.InstallPath); err != nil {
		return fmt.Errorf("install: %w", err)
	}
	fmt.Fprintf(opts.Out, "Done. infra is now %s.\n", m.Version)
	return nil
}

func download(ctx context.Context, url, dest string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return fmt.Errorf("download %s: %w", url, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("download %s: HTTP %d", url, resp.StatusCode)
	}
	f, err := os.OpenFile(dest, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0644)
	if err != nil {
		return err
	}
	defer f.Close()
	if _, err := io.Copy(f, resp.Body); err != nil {
		return err
	}
	return nil
}

func sha256File(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
```

Merge the new imports into the existing import block.

- [ ] **Step 4: Run tests to verify they pass**

```sh
cd cli && go test ./internal/cmd/... -run TestRunFromMirror
```
Expected: PASS, all 3 tests.

- [ ] **Step 5: Commit**

```sh
git add cli/internal/cmd/update.go cli/internal/cmd/update_test.go
git commit -m "cli/update: add runFromMirror (manifest-based default path)"
```

---

### Task 6: Wire flags + default path into cobra command

**Files:**
- Modify: `cli/internal/cmd/update.go`

- [ ] **Step 1: Update flag set + RunE**

Replace the current `newUpdateCmd` body to add new flags and dispatch on `--from-source`. The new function:

```go
const defaultMirrorURL = "https://infra-bin.lan/manifest.json"

func newUpdateCmd() *cobra.Command {
	var check bool
	var yes bool
	var ref string
	var fromSource bool
	var mirrorURL string

	c := &cobra.Command{
		Use:   "update",
		Short: "Update the infra binary",
		Long:  "Updates the infra binary from the LAN release mirror by default. Use --from-source to build from a local repo checkout instead.",
		RunE: func(cmd *cobra.Command, _ []string) error {
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
			defer cancel()

			if fromSource {
				return runFromSource(ctx, cmd, runFromSourceOpts{
					Check: check,
					Yes:   yes,
					Ref:   ref,
				})
			}

			installTarget, err := installPath()
			if err != nil {
				return err
			}
			currentVer := cmd.Root().Annotations["version"]
			url := mirrorURL
			if env := os.Getenv("INFRA_MIRROR_URL"); env != "" && url == defaultMirrorURL {
				url = env
			}
			return runFromMirror(ctx, runFromMirrorOpts{
				MirrorURL:   url,
				CurrentVer:  currentVer,
				InstallPath: installTarget,
				Check:       check,
				Yes:         yes,
				Out:         os.Stdout,
			})
		},
	}
	c.Flags().BoolVar(&check, "check", false, "Report status only; don't download or build")
	c.Flags().BoolVarP(&yes, "yes", "y", false, "Skip confirmation")
	c.Flags().StringVar(&ref, "ref", "", "(--from-source only) branch or tag to update to")
	c.Flags().BoolVar(&fromSource, "from-source", false, "Build from a local repo checkout instead of the mirror")
	c.Flags().StringVar(&mirrorURL, "mirror", defaultMirrorURL, "Manifest URL (overrides INFRA_MIRROR_URL when set explicitly)")
	return c
}
```

- [ ] **Step 2: Run all tests**

```sh
cd cli && go test ./...
```
Expected: PASS. The mirror tests still pass; existing flow tests unaffected.

- [ ] **Step 3: Smoke-test both paths**

```sh
cd cli && go build -o /tmp/infra-test ./cmd/infra
/tmp/infra-test update --check --mirror http://127.0.0.1:1/manifest.json 2>&1 | head -5
```
Expected: error message starting with `mirror at ... unreachable`. The `--from-source` path should also still work:

```sh
/tmp/infra-test update --from-source --check
```
Expected: `Up-to-date: ...` or current/origin status.

- [ ] **Step 4: Commit**

```sh
git add cli/internal/cmd/update.go
git commit -m "cli/update: default to mirror; add --from-source, --mirror flags"
```

---

## Phase C — Passive Update Check

### Task 7: `updatecheck` cache module

**Files:**
- Create: `cli/internal/updatecheck/cache.go`
- Create: `cli/internal/updatecheck/cache_test.go`

- [ ] **Step 1: Write the failing test**

```go
// cli/internal/updatecheck/cache_test.go
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
```

- [ ] **Step 2: Run tests to verify they fail**

```sh
cd cli && go test ./internal/updatecheck/...
```
Expected: FAIL — `package not found` or `undefined: readCache/writeCache/stale`.

- [ ] **Step 3: Write minimal implementation**

```go
// cli/internal/updatecheck/cache.go

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
```

- [ ] **Step 4: Run tests to verify they pass**

```sh
cd cli && go test ./internal/updatecheck/...
```
Expected: PASS, both tests.

- [ ] **Step 5: Commit**

```sh
git add cli/internal/updatecheck/
git commit -m "cli/updatecheck: add cache file IO + TTL helper"
```

---

### Task 8: `updatecheck.Run` — orchestrator

**Files:**
- Create: `cli/internal/updatecheck/check.go`
- Create: `cli/internal/updatecheck/check_test.go`

- [ ] **Step 1: Write the failing test**

```go
// cli/internal/updatecheck/check_test.go
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
		Now:       func() time.Time { return time.Date(2026, 4, 29, 12, 0, 0, 0, time.UTC) },
	}
	checker.Refresh(ctx) // synchronous in tests

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
```

- [ ] **Step 2: Run tests to verify they fail**

```sh
cd cli && go test ./internal/updatecheck/... -run TestRun_WritesCache -v
```
Expected: FAIL — `undefined: Checker`.

- [ ] **Step 3: Write minimal implementation**

```go
// cli/internal/updatecheck/check.go
package updatecheck

import (
	"context"
	"fmt"
	"io"
	"os"
	"time"

	"github.com/psychonaut0/infra/cli/internal/manifest"
)

// Checker performs a passive, cached version check against the mirror.
//
// Lifecycle:
//   - Refresh(ctx) (typically in a goroutine from PersistentPreRun) updates
//     the cache file if the existing entry is older than TTL.
//   - Footer() (called from PersistentPostRun) reads the cache and, if
//     enabled and a newer version is known, prints a one-line stderr footer.
type Checker struct {
	MirrorURL      string
	CachePath      string
	TTL            time.Duration
	CurrentVersion string
	Enabled        bool
	Out            io.Writer
	Now            func() time.Time
}

func (c *Checker) now() time.Time {
	if c.Now != nil {
		return c.Now()
	}
	return time.Now()
}

// Refresh fetches the manifest if the cache is stale and writes the new
// entry. All errors are silent — the check must never disrupt the user's
// command.
func (c *Checker) Refresh(ctx context.Context) {
	if !c.Enabled {
		return
	}
	if e, err := readCache(c.CachePath); err == nil {
		if !stale(e.LastCheckAt, c.TTL, c.now()) {
			return
		}
	}
	m, err := manifest.Fetch(ctx, c.MirrorURL)
	if err != nil {
		return
	}
	_ = writeCache(c.CachePath, cacheEntry{
		LastCheckAt:   c.now(),
		LatestVersion: m.Version,
	})
}

// Footer prints a single-line "update available" notice to c.Out if a newer
// version has been observed in cache. No-op when disabled, when the cache is
// missing, or when the cached version is not newer than CurrentVersion.
func (c *Checker) Footer() {
	if !c.Enabled || c.Out == nil {
		return
	}
	e, err := readCache(c.CachePath)
	if err != nil {
		return
	}
	if !manifest.Newer(e.LatestVersion, c.CurrentVersion) {
		return
	}
	fmt.Fprintf(c.Out, "[infra update available: %s → %s — run 'infra update']\n",
		c.CurrentVersion, e.LatestVersion)
}

// New returns a Checker pre-configured with default cache path and the
// suppression rules from the spec (env opt-out, dev build, non-TTY stderr).
func New(currentVersion string) *Checker {
	c := &Checker{
		MirrorURL:      "https://infra-bin.lan/manifest.json",
		TTL:            24 * time.Hour,
		CurrentVersion: currentVersion,
		Out:            os.Stderr,
		Enabled:        defaultEnabled(currentVersion),
	}
	if p, err := cachePath(); err == nil {
		c.CachePath = p
	}
	if env := os.Getenv("INFRA_MIRROR_URL"); env != "" {
		c.MirrorURL = env
	}
	return c
}

func defaultEnabled(currentVersion string) bool {
	if os.Getenv("INFRA_NO_UPDATE_CHECK") == "1" {
		return false
	}
	if currentVersion == "dev" || currentVersion == "" {
		return false
	}
	return isTerminal(os.Stderr)
}
```

Add a tiny TTY helper file:

```go
// cli/internal/updatecheck/tty.go
package updatecheck

import (
	"os"

	"golang.org/x/sys/unix"
)

func isTerminal(f *os.File) bool {
	_, err := unix.IoctlGetTermios(int(f.Fd()), unix.TCGETS)
	return err == nil
}
```

(`golang.org/x/sys` is already an indirect dep per `go.mod`. Promote it: `cd cli && go get golang.org/x/sys/unix`. Then `go mod tidy`.)

- [ ] **Step 4: Run tests to verify they pass**

```sh
cd cli && go mod tidy && go test ./internal/updatecheck/...
```
Expected: PASS, all 4 tests.

- [ ] **Step 5: Commit**

```sh
git add cli/internal/updatecheck/ cli/go.mod cli/go.sum
git commit -m "cli/updatecheck: add Checker (Refresh + Footer)"
```

---

### Task 9: Wire `updatecheck` into root cobra

**Files:**
- Modify: `cli/internal/cmd/root.go`

- [ ] **Step 1: Edit root.go**

```go
package cmd

import (
	"context"
	"fmt"
	"sync"
	"time"

	"github.com/psychonaut0/infra/cli/internal/updatecheck"
	"github.com/spf13/cobra"
)

type BuildInfo struct {
	Version string
	Commit  string
}

func Execute(info BuildInfo) error {
	checker := updatecheck.New(info.Version)
	var wg sync.WaitGroup

	root := &cobra.Command{
		Use:   "infra",
		Short: "Homelab infrastructure CLI",
		Long:  "infra wraps SSH + docker compose operations across the homelab.",
		Annotations: map[string]string{
			"version": info.Version,
			"commit":  info.Commit,
		},
		SilenceUsage: true,
		PersistentPreRun: func(cmd *cobra.Command, _ []string) {
			// Skip the network call entirely on update commands and on -h/--help.
			if cmd.Name() == "update" || cmd.Name() == "help" {
				return
			}
			wg.Add(1)
			go func() {
				defer wg.Done()
				ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
				defer cancel()
				checker.Refresh(ctx)
			}()
		},
		PersistentPostRun: func(_ *cobra.Command, _ []string) {
			wg.Wait()
			checker.Footer()
		},
	}
	root.AddCommand(newVersionCmd())
	root.AddCommand(newLsCmd())
	root.AddCommand(newStatusCmd())
	root.AddCommand(newLogsCmd())
	root.AddCommand(newRestartCmd())
	root.AddCommand(newDeployCmd())
	root.AddCommand(newCtCmd())
	root.AddCommand(newUpdateCmd())
	return root.Execute()
}

func newVersionCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "version",
		Short: "Print version information",
		Run: func(cmd *cobra.Command, _ []string) {
			a := cmd.Root().Annotations
			fmt.Printf("infra %s (%s)\n", a["version"], a["commit"])
		},
	}
}
```

- [ ] **Step 2: Build and verify nothing broke**

```sh
cd cli && go build ./... && go test ./...
```
Expected: build success; tests pass.

- [ ] **Step 3: Smoke test**

```sh
cd cli && go build -o /tmp/infra-test ./cmd/infra
INFRA_MIRROR_URL=http://127.0.0.1:1/manifest.json /tmp/infra-test ls
```
Expected: `infra ls` output prints normally; no error from the failing background fetch (silent failure is correct).

```sh
INFRA_NO_UPDATE_CHECK=1 /tmp/infra-test ls
```
Expected: same `infra ls` output; faster (no goroutine spawned).

- [ ] **Step 4: Commit**

```sh
git add cli/internal/cmd/root.go
git commit -m "cli: wire passive update check into root cobra"
```

---

## Phase D — GitHub Actions

### Task 10: CI workflow (path-filtered, no publish)

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Write the workflow**

```yaml
# .github/workflows/ci.yml
name: ci

on:
  push:
    branches: [master]
    paths:
      - 'cli/**'
      - '.github/workflows/ci.yml'
  pull_request:
    paths:
      - 'cli/**'
      - '.github/workflows/ci.yml'

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: cli
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version-file: cli/go.mod
          cache-dependency-path: cli/go.sum
      - run: go vet ./...
      - run: go test ./...

  build-smoke:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        goarch: [amd64, arm64]
    defaults:
      run:
        working-directory: cli
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version-file: cli/go.mod
          cache-dependency-path: cli/go.sum
      - name: Build
        env:
          GOOS: linux
          GOARCH: ${{ matrix.goarch }}
        run: go build -trimpath -o /tmp/infra-${{ matrix.goarch }} ./cmd/infra
```

- [ ] **Step 2: Verify YAML syntax locally**

```sh
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```sh
git add .github/workflows/ci.yml
git commit -m "ci: add path-filtered Go CI workflow"
```

- [ ] **Step 4: Verify in GitHub**

After pushing later (combined with other commits), the next CLI-touching push or PR should trigger this workflow. A change limited to e.g. `stacks/` should NOT trigger it. Verified post-push in Task 19.

---

### Task 11: Release workflow (tag-triggered)

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Write the workflow**

```yaml
# .github/workflows/release.yml
name: release

on:
  push:
    tags:
      - 'v*'

permissions:
  contents: write   # required to create/update GH Releases

jobs:
  build:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        include:
          - goos: linux
            goarch: amd64
          - goos: linux
            goarch: arm64
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version-file: cli/go.mod
          cache-dependency-path: cli/go.sum
      - name: Build
        working-directory: cli
        env:
          GOOS: ${{ matrix.goos }}
          GOARCH: ${{ matrix.goarch }}
          CGO_ENABLED: 0
          TAG: ${{ github.ref_name }}
          SHA: ${{ github.sha }}
        run: |
          SHORT_SHA=$(echo "$SHA" | cut -c1-7)
          OUT="../infra-${GOOS}-${GOARCH}"
          go build \
            -trimpath \
            -ldflags "-s -w -X main.Version=${TAG} -X main.Commit=${SHORT_SHA}" \
            -o "${OUT}" \
            ./cmd/infra
      - name: Compute sha256
        run: |
          cd ${{ github.workspace }}
          sha256sum infra-${{ matrix.goos }}-${{ matrix.goarch }} \
            > infra-${{ matrix.goos }}-${{ matrix.goarch }}.sha256
      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: infra-${{ matrix.goos }}-${{ matrix.goarch }}
          path: |
            infra-${{ matrix.goos }}-${{ matrix.goarch }}
            infra-${{ matrix.goos }}-${{ matrix.goarch }}.sha256
          retention-days: 7

  release:
    needs: build
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/download-artifact@v4
        with:
          path: artifacts
          merge-multiple: true
      - name: List artifacts
        run: ls -la artifacts/
      - name: Create GH Release
        uses: softprops/action-gh-release@v2
        with:
          tag_name: ${{ github.ref_name }}
          generate_release_notes: true
          files: |
            artifacts/infra-linux-amd64
            artifacts/infra-linux-amd64.sha256
            artifacts/infra-linux-arm64
            artifacts/infra-linux-arm64.sha256
```

- [ ] **Step 2: Verify YAML syntax locally**

```sh
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"
```
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```sh
git add .github/workflows/release.yml
git commit -m "ci: add tag-triggered release workflow with linux amd64+arm64 binaries"
```

(Real verification happens in Task 19, when the first tag is pushed.)

---

## Phase E — Mirror Service Files

### Task 12: `sync.sh`

**Files:**
- Create: `stacks/ct-mgmt/infra-mirror/sync.sh`

- [ ] **Step 1: Write the script**

```sh
#!/bin/sh
# infra release mirror sync — polls GitHub Releases for the latest infra
# CLI tag and re-publishes the artifacts to the LAN-served Caddy directory.
#
# Run by infra-mirror.timer (every 5 min). Exits 0 if there's nothing new or
# the publish succeeded; non-zero on any failure (existing manifest is left
# untouched).
set -eu

REPO="psychonaut0/infra"
TOKEN_FILE="/etc/infra-mirror/token"
STATE_FILE="/etc/infra-mirror/state.json"
PUBLISH_DIR="/var/www/infra-bin"
INSTALL_SCRIPT_SRC="/opt/infra-mirror/install.sh"

require() {
    command -v "$1" >/dev/null 2>&1 || { echo "missing dependency: $1" >&2; exit 2; }
}
require curl
require jq
require sha256sum

[ -r "$TOKEN_FILE" ] || { echo "missing $TOKEN_FILE" >&2; exit 2; }
TOKEN=$(cat "$TOKEN_FILE")
AUTH="Authorization: Bearer ${TOKEN}"
ACCEPT="Accept: application/vnd.github+json"

API="https://api.github.com/repos/${REPO}/releases/latest"
release_json=$(curl -fsSL -H "$AUTH" -H "$ACCEPT" "$API")

tag=$(echo "$release_json" | jq -r '.tag_name')
[ -n "$tag" ] && [ "$tag" != "null" ] || { echo "no tag in release JSON" >&2; exit 1; }

last_tag=""
[ -f "$STATE_FILE" ] && last_tag=$(jq -r '.last_tag // ""' < "$STATE_FILE" 2>/dev/null || true)
if [ "$tag" = "$last_tag" ]; then
    exit 0
fi

published_at=$(echo "$release_json" | jq -r '.published_at')
sha=$(echo "$release_json" | jq -r '.target_commitish' | cut -c1-7)

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

ARCHES="amd64 arm64"
for arch in $ARCHES; do
    bin_name="infra-linux-${arch}"
    bin_url=$(echo "$release_json" | jq -r ".assets[] | select(.name==\"${bin_name}\") | .url")
    sha_url=$(echo "$release_json" | jq -r ".assets[] | select(.name==\"${bin_name}.sha256\") | .url")
    [ -n "$bin_url" ] && [ "$bin_url" != "null" ] || { echo "asset ${bin_name} not found in release" >&2; exit 1; }

    curl -fsSL -H "$AUTH" -H "Accept: application/octet-stream" "$bin_url"  -o "$work/$bin_name"
    curl -fsSL -H "$AUTH" -H "Accept: application/octet-stream" "$sha_url" -o "$work/$bin_name.sha256"

    expected=$(awk '{print $1}' "$work/$bin_name.sha256")
    actual=$(sha256sum "$work/$bin_name" | awk '{print $1}')
    [ "$expected" = "$actual" ] || { echo "sha256 mismatch for $bin_name" >&2; exit 1; }
    echo "$arch:$expected" >> "$work/sums"
done

# Stage the publish dir.
mkdir -p "$PUBLISH_DIR/linux/amd64" "$PUBLISH_DIR/linux/arm64"
for arch in $ARCHES; do
    bin_name="infra-linux-${arch}"
    install -m 0755 "$work/$bin_name" "$PUBLISH_DIR/linux/${arch}/infra.new"
    mv "$PUBLISH_DIR/linux/${arch}/infra.new" "$PUBLISH_DIR/linux/${arch}/infra"
done

# Refresh install.sh.
[ -f "$INSTALL_SCRIPT_SRC" ] && install -m 0755 "$INSTALL_SCRIPT_SRC" "$PUBLISH_DIR/install.sh"

# Build manifest.
sum_amd64=$(awk -F: '$1=="amd64"{print $2}' "$work/sums")
sum_arm64=$(awk -F: '$1=="arm64"{print $2}' "$work/sums")
cat > "$work/manifest.json" <<EOF
{
  "version": "${tag}",
  "commit": "${sha}",
  "published_at": "${published_at}",
  "binaries": {
    "linux/amd64": {
      "url": "https://infra-bin.lan/linux/amd64/infra",
      "sha256": "${sum_amd64}"
    },
    "linux/arm64": {
      "url": "https://infra-bin.lan/linux/arm64/infra",
      "sha256": "${sum_arm64}"
    }
  }
}
EOF
mv "$work/manifest.json" "$PUBLISH_DIR/manifest.json"

# Persist state.
mkdir -p "$(dirname "$STATE_FILE")"
echo "{\"last_tag\":\"${tag}\"}" > "$STATE_FILE"

echo "published ${tag}"
```

- [ ] **Step 2: Static-check the script**

```sh
shellcheck stacks/ct-mgmt/infra-mirror/sync.sh
```
Fix any errors (warnings about `[ -n "$X" ]` patterns are usually fine; address SC2086 quoting if it flags).

- [ ] **Step 3: Make executable**

```sh
chmod +x stacks/ct-mgmt/infra-mirror/sync.sh
```

- [ ] **Step 4: Commit**

```sh
git add stacks/ct-mgmt/infra-mirror/sync.sh
git commit -m "stacks/ct-mgmt: add infra-mirror sync.sh"
```

---

### Task 13: `install.sh` (consumer bootstrap)

**Files:**
- Create: `stacks/ct-mgmt/infra-mirror/install.sh`

- [ ] **Step 1: Write the script**

```sh
#!/bin/sh
# infra CLI bootstrap installer — fetched as `curl -fsSL https://infra-bin.lan/install.sh | sh`.
# Detects arch, downloads the matching binary from the LAN mirror, verifies
# its sha256 against manifest.json, and installs to /usr/local/bin/infra.
set -eu

MIRROR="${INFRA_MIRROR:-https://infra-bin.lan}"
DEST="${INFRA_INSTALL_DIR:-/usr/local/bin}"

case "$(uname -m)" in
    x86_64)  ARCH=amd64 ;;
    aarch64) ARCH=arm64 ;;
    *) echo "unsupported arch: $(uname -m)" >&2; exit 1 ;;
esac

require() {
    command -v "$1" >/dev/null 2>&1 || { echo "missing dependency: $1" >&2; exit 2; }
}
require curl
require sha256sum
require jq

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

curl -fsSL "$MIRROR/linux/$ARCH/infra" -o "$tmp"
expected=$(curl -fsSL "$MIRROR/manifest.json" | jq -r ".binaries[\"linux/${ARCH}\"].sha256")
actual=$(sha256sum "$tmp" | awk '{print $1}')
if [ "$expected" != "$actual" ]; then
    echo "checksum mismatch (got $actual, expected $expected)" >&2
    exit 1
fi

install -m 0755 "$tmp" "$DEST/infra"
echo "installed $DEST/infra ($("$DEST/infra" version 2>/dev/null || echo 'no version subcommand yet'))"
```

- [ ] **Step 2: Static-check**

```sh
shellcheck stacks/ct-mgmt/infra-mirror/install.sh
```

- [ ] **Step 3: Make executable**

```sh
chmod +x stacks/ct-mgmt/infra-mirror/install.sh
```

- [ ] **Step 4: Commit**

```sh
git add stacks/ct-mgmt/infra-mirror/install.sh
git commit -m "stacks/ct-mgmt: add infra-mirror install.sh bootstrap"
```

---

### Task 14: systemd service unit

**Files:**
- Create: `stacks/ct-mgmt/infra-mirror/infra-mirror.service`

- [ ] **Step 1: Write the unit**

```ini
[Unit]
Description=infra CLI release mirror sync
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/opt/infra-mirror/sync.sh
StandardOutput=journal
StandardError=journal
ProtectSystem=full
ProtectHome=true
PrivateTmp=true
NoNewPrivileges=true
ReadWritePaths=/var/www/infra-bin /etc/infra-mirror
```

- [ ] **Step 2: Commit**

```sh
git add stacks/ct-mgmt/infra-mirror/infra-mirror.service
git commit -m "stacks/ct-mgmt: add infra-mirror.service"
```

---

### Task 15: systemd timer unit

**Files:**
- Create: `stacks/ct-mgmt/infra-mirror/infra-mirror.timer`

- [ ] **Step 1: Write the unit**

```ini
[Unit]
Description=infra CLI release mirror sync (every 5 min)

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Persistent=true
Unit=infra-mirror.service

[Install]
WantedBy=timers.target
```

- [ ] **Step 2: Commit**

```sh
git add stacks/ct-mgmt/infra-mirror/infra-mirror.timer
git commit -m "stacks/ct-mgmt: add infra-mirror.timer (5 min cadence)"
```

---

### Task 16: README with deploy steps

**Files:**
- Create: `stacks/ct-mgmt/infra-mirror/README.md`

- [ ] **Step 1: Write the README**

````markdown
# infra-mirror

Pulls the latest GitHub Release of `psychonaut0/infra` (CLI tags `v*`) and
re-publishes the binaries to a LAN URL behind ct-mgmt's Caddy. Hosts use
`https://infra-bin.lan/manifest.json` to detect new versions and
`https://infra-bin.lan/linux/<arch>/infra` to download.

## Architecture

```
GH Actions release.yml  →  GH Release (private)
                                │  (PAT-auth poll)
                                ▼
                         ct-mgmt: sync.sh
                          (systemd timer, 5 min)
                                │
                                ▼
                         /var/www/infra-bin/  ←─ bind-mounted into caddy CT as /srv/infra-bin
                                │
                                ▼
                         Caddy → http://infra-bin.lan
```

## First-time deploy on ct-mgmt

These steps are manual and run once on a fresh host. The repo is the source
of truth for `sync.sh`, `install.sh`, and the units.

```sh
# 1. From your workstation, copy the artifacts to ct-mgmt.
scp stacks/ct-mgmt/infra-mirror/sync.sh         ct-mgmt:/opt/infra-mirror/sync.sh
scp stacks/ct-mgmt/infra-mirror/install.sh      ct-mgmt:/opt/infra-mirror/install.sh
scp stacks/ct-mgmt/infra-mirror/infra-mirror.service ct-mgmt:/etc/systemd/system/
scp stacks/ct-mgmt/infra-mirror/infra-mirror.timer   ct-mgmt:/etc/systemd/system/

# 2. SSH to ct-mgmt and finalize.
ssh ct-mgmt
mkdir -p /opt/infra-mirror /etc/infra-mirror /var/www/infra-bin
chmod 0755 /opt/infra-mirror/sync.sh /opt/infra-mirror/install.sh
apt-get install -y jq curl   # if not already present

# Drop the fine-grained PAT (Contents:Read, Metadata:Read on psychonaut0/infra).
install -m 0600 /dev/stdin /etc/infra-mirror/token <<<'<paste-PAT-here>'

systemctl daemon-reload
systemctl enable --now infra-mirror.timer
systemctl start infra-mirror.service     # one-shot first sync
journalctl -u infra-mirror -n 50         # confirm "published vX.Y.Z"
```

## Caddy bind-mount

`stacks/ct-mgmt/docker-compose.yml` bind-mounts `/var/www/infra-bin` into the
caddy container as `/srv/infra-bin:ro`. The Caddyfile site block:

```caddy
http://infra-bin.lan {
    root * /srv/infra-bin
    file_server
    encode gzip
}
```

After the first deploy, `cd /opt/stacks/ct-mgmt && docker compose up -d caddy`
on ct-mgmt to apply the new mount and config.

## DNS

Add a Pi-hole local record on ct-dns: `infra-bin.lan → 192.168.3.12`
(Settings → Local DNS → DNS Records).

## Refreshing on changes

When `sync.sh` or `install.sh` is updated in the repo, rerun the matching
`scp` line and `systemctl start infra-mirror.service` to pick it up. The
units rarely change; if they do, also run `systemctl daemon-reload`.

## Verifying

From any LAN host:

```sh
curl -s https://infra-bin.lan/manifest.json | jq
```

A fresh CT can bootstrap with:

```sh
curl -fsSL https://infra-bin.lan/install.sh | sh
```

## Troubleshooting

- `journalctl -u infra-mirror -f` — live log of the sync runs.
- `systemctl list-timers infra-mirror.timer` — next/last fire times.
- 401 from GH API → PAT invalid or scope insufficient. Regenerate at
  github.com/settings/tokens, replace `/etc/infra-mirror/token`.
- Caddy 404 → check the bind mount is mounted (`docker exec caddy ls /srv/infra-bin`)
  and the Caddyfile site block exists.
````

- [ ] **Step 2: Commit**

```sh
git add stacks/ct-mgmt/infra-mirror/README.md
git commit -m "stacks/ct-mgmt: document infra-mirror deploy"
```

---

## Phase F — Caddy + Compose Wiring

### Task 17: Bind-mount + Caddy site block

**Files:**
- Modify: `stacks/ct-mgmt/docker-compose.yml`
- Modify: `stacks/ct-mgmt/Caddyfile`

- [ ] **Step 1: Edit `docker-compose.yml` caddy service**

Change the `caddy` service `volumes:` block from:

```yaml
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
      - caddy-config:/config
```

to:

```yaml
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
      - caddy-config:/config
      - /var/www/infra-bin:/srv/infra-bin:ro
```

- [ ] **Step 2: Append to `Caddyfile`**

Append at the end of `stacks/ct-mgmt/Caddyfile`:

```caddy

http://infra-bin.lan {
	root * /srv/infra-bin
	file_server
	encode gzip
}
```

- [ ] **Step 3: Commit**

```sh
git add stacks/ct-mgmt/docker-compose.yml stacks/ct-mgmt/Caddyfile
git commit -m "stacks/ct-mgmt: serve infra-bin.lan from Caddy bind mount"
```

---

## Phase G — Cleanup + Docs

### Task 18: Remove `make deploy`; update CLAUDE.md

**Files:**
- Modify: `cli/Makefile`
- Modify: `CLAUDE.md` (the infra repo root one, at `/home/psy/Documents/personal/infra/CLAUDE.md`)

- [ ] **Step 1: Edit `cli/Makefile`**

Remove the `TARGETS := …` line and the `deploy:` recipe. The trimmed file:

```makefile
VERSION := $(shell git -C .. describe --tags --always --dirty 2>/dev/null || echo dev)
COMMIT  := $(shell git -C .. rev-parse --short HEAD 2>/dev/null || echo unknown)
LDFLAGS := -X main.Version=$(VERSION) -X main.Commit=$(COMMIT)
PREFIX  ?= $(HOME)/.local/bin

.DEFAULT_GOAL := install

.PHONY: build install test fmt clean

build:
	mkdir -p bin
	go build -ldflags "$(LDFLAGS)" -o bin/infra ./cmd/infra

install: build
	install -m 755 bin/infra $(PREFIX)/infra
	@echo "Installed: $(PREFIX)/infra ($(VERSION))"

test:
	go test ./...

fmt:
	gofmt -w .

clean:
	rm -rf bin
```

- [ ] **Step 2: Edit `CLAUDE.md` ct-mgmt section**

Find the `### ct-mgmt` block. Replace its `**Stack:**` line and add a note about infra-mirror. Specifically, modify the `**Stack:**` and `**Config notes:**` lines so the section reads (only the relevant lines change — keep the rest):

```markdown
- **Stack:** `/opt/stacks/ct-mgmt/docker-compose.yml` (local copy: `stacks/ct-mgmt/`). Also runs the native systemd `infra-mirror.timer` from `stacks/ct-mgmt/infra-mirror/` for CLI release distribution at `infra-bin.lan`.
```

In the **Services** section near the bottom of the file, add a new line:

```markdown
infra CLI release mirror runs natively on ct-mgmt as a systemd timer (every 5 min) and is served via Caddy at http://infra-bin.lan. Pulls GitHub Release artifacts and re-publishes to the LAN. Source + deploy notes: `stacks/ct-mgmt/infra-mirror/`.
```

- [ ] **Step 3: Build to verify nothing broke**

```sh
cd cli && make build
```
Expected: `bin/infra` produced.

- [ ] **Step 4: Commit**

```sh
git add cli/Makefile CLAUDE.md
git commit -m "cli/Makefile: drop deploy target; CLAUDE.md: document infra-mirror"
```

---

## Phase H — Manual Deployment + First Release

These steps are not committed code; they're the operational rollout. Execute each one in order. If any step fails, stop and fix before proceeding — none of the later steps are useful in isolation.

### Task 19: Manual — push CI changes and verify CI runs

- [ ] **Step 1: Push the master branch up to and including the previous commit**

```sh
git push origin master
```

- [ ] **Step 2: Watch CI in the GH Actions tab**

`ci.yml` should run on this push (because `cli/**` and `.github/workflows/ci.yml` were touched). `release.yml` should NOT run (no tag).

```sh
gh run list --workflow ci.yml --limit 3
```
Expected: latest run `success`.

- [ ] **Step 3: Sanity-check the path filter**

Make a no-op commit touching only `docs/`:

```sh
echo "" >> docs/hardware.md
git commit -am "docs: noop to verify CI path filter"
git push
gh run list --workflow ci.yml --limit 3
```
Expected: no new run for this commit. Then:

```sh
git revert HEAD --no-edit && git push
```
(Reverts the noop so we don't keep churn.)

---

### Task 20: Manual — create fine-grained PAT

- [ ] **Step 1: Generate the token**

In github.com → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token:

- Token name: `ct-mgmt infra-mirror`
- Expiration: 1 year
- Repository access: only `psychonaut0/infra`
- Permissions:
  - Repository → Contents → Read-only
  - Repository → Metadata → Read-only (mandatory)
- Generate, copy the token

- [ ] **Step 2: Note the expiration date for renewal calendar**

(Optional but recommended.)

---

### Task 21: Manual — deploy mirror to ct-mgmt

Follows the steps in `stacks/ct-mgmt/infra-mirror/README.md`.

- [ ] **Step 1: Copy files**

```sh
ssh ct-mgmt 'mkdir -p /opt/infra-mirror /etc/infra-mirror /var/www/infra-bin'
scp stacks/ct-mgmt/infra-mirror/sync.sh         ct-mgmt:/opt/infra-mirror/sync.sh
scp stacks/ct-mgmt/infra-mirror/install.sh      ct-mgmt:/opt/infra-mirror/install.sh
scp stacks/ct-mgmt/infra-mirror/infra-mirror.service ct-mgmt:/etc/systemd/system/
scp stacks/ct-mgmt/infra-mirror/infra-mirror.timer   ct-mgmt:/etc/systemd/system/
```

- [ ] **Step 2: Install deps + place token**

```sh
ssh ct-mgmt 'apt-get install -y jq curl'
ssh ct-mgmt 'install -m 0600 /dev/stdin /etc/infra-mirror/token' <<<'<paste-PAT-here>'
ssh ct-mgmt 'chmod 0755 /opt/infra-mirror/sync.sh /opt/infra-mirror/install.sh'
```

- [ ] **Step 3: Enable timer**

```sh
ssh ct-mgmt 'systemctl daemon-reload && systemctl enable --now infra-mirror.timer'
```

- [ ] **Step 4: Apply Caddy bind mount**

```sh
ssh ct-mgmt 'cd /opt/stacks/ct-mgmt && git -C /opt/stacks/ct-mgmt pull || true'
# If /opt/stacks/ct-mgmt/ is rsynced from the repo via your usual mechanism,
# apply that mechanism here instead.
ssh ct-mgmt 'cd /opt/stacks/ct-mgmt && docker compose up -d caddy'
```

(If your deploy convention is different — e.g., `infra deploy ct-mgmt` once the binary exists on ct-mgmt — use that. The salient bit is that `/var/www/infra-bin` must be bind-mounted into the running caddy container.)

---

### Task 22: Manual — DNS record on ct-dns

- [ ] **Step 1: Add Pi-hole local record**

Browse to `http://dns.lan/admin/` → Settings → Local DNS Records → add:
- Domain: `infra-bin.lan`
- IP: `192.168.3.12`

- [ ] **Step 2: Verify resolution**

```sh
dig +short infra-bin.lan @192.168.3.5
```
Expected: `192.168.3.12`.

---

### Task 23: Manual — first release tag + verify mirror

- [ ] **Step 1: Tag the current master**

```sh
git tag v0.4.0
git push origin v0.4.0
```

- [ ] **Step 2: Watch release pipeline**

```sh
gh run list --workflow release.yml --limit 3
gh run watch <run-id>
```
Expected: success in <2 min, two binaries + two `.sha256` files attached to GH Release `v0.4.0`.

- [ ] **Step 3: Force a sync on ct-mgmt**

```sh
ssh ct-mgmt 'systemctl start infra-mirror.service'
ssh ct-mgmt 'journalctl -u infra-mirror -n 30 --no-pager'
```
Expected: `published v0.4.0`.

- [ ] **Step 4: Verify served files from blvckmain**

```sh
curl -s https://infra-bin.lan/manifest.json | jq
curl -fsSI https://infra-bin.lan/linux/amd64/infra | head -3
```
Expected: manifest with `"version": "v0.4.0"`; HTTP/200 on the binary.

---

### Task 24: Manual — install on a CT that has no Go and verify update path

- [ ] **Step 1: Bootstrap on ct-tunnel** (smallest CT, no Go installed)

```sh
ssh ct-tunnel 'curl -fsSL https://infra-bin.lan/install.sh | sh'
ssh ct-tunnel 'infra version'
```
Expected: `infra v0.4.0 (...)`.

- [ ] **Step 2: Verify passive footer appears (optional)**

This requires a v0.4.1 or higher to exist. Skip until a second release lands; later run any `infra ls` and watch for the `[update available: ...]` footer.

- [ ] **Step 3: Verify active update path with a dummy newer version**

Tag a noop release `v0.4.1`:

```sh
git commit --allow-empty -m "release: v0.4.1"
git tag v0.4.1
git push origin master v0.4.1
```

Wait for release.yml + 5 min mirror tick (or trigger sync manually):

```sh
ssh ct-mgmt 'systemctl start infra-mirror.service'
ssh ct-tunnel 'infra update -y'
ssh ct-tunnel 'infra version'
```
Expected: `infra v0.4.1 (...)`.

---

### Task 25: Final sweep

- [ ] **Step 1: Update `infra` on every host**

For each host in CLAUDE.md's network layout (proxmoxmain, proxmoxnode, ct-* CTs you use the CLI on, blvckmain, Termux):

```sh
# Servers / CTs:
ssh <host> 'curl -fsSL https://infra-bin.lan/install.sh | sh'
ssh <host> 'infra version'

# blvckmain (you):
curl -fsSL https://infra-bin.lan/install.sh | sh    # installs to /usr/local/bin/infra
infra version
```

(Termux requires being on home WiFi; off-LAN updates are out of scope per the spec.)

- [ ] **Step 2: Confirm CI minute usage looks healthy**

```sh
gh api /repos/psychonaut0/infra/actions/cache/usage
```
Sanity check that nothing's runaway.

- [ ] **Step 3: Mark plan complete**

Done.

---

## Self-Review Notes

Spec coverage check (each requirement → task):

- ci.yml path-filtered, no publish → Task 10
- release.yml tag-triggered, matrix amd64+arm64, sha256 + GH Release → Task 11
- Mirror service: sync.sh, systemd unit + timer, Caddy site, PAT, `manifest.json` → Tasks 12–17, 21
- `infra update` manifest-default + `--from-source` + `--mirror` flags → Tasks 4–6
- Passive 24h check with footer + suppression rules → Tasks 7–9
- `install.sh` curl-pipe bootstrap → Task 13
- DNS record `infra-bin.lan → ct-mgmt` → Task 22
- Drop `make deploy` → Task 18
- CLAUDE.md update → Task 18
- Cut first real release & bootstrap fleet → Tasks 23–25

Type consistency check: `Manifest`, `Binary`, `Newer`, `Fetch`, `Parse`, `ForArch` are defined in Tasks 1–3 and consumed in Tasks 5, 8 with matching signatures. `Checker` fields used in Task 9 (`MirrorURL`, `CachePath`, `TTL`, `CurrentVersion`, `Enabled`, `Out`, `Now`) match the struct definition in Task 8. `runFromMirrorOpts` fields match between Task 5 (definition + tests) and Task 6 (call site).

No placeholders, no "see task N", no "add appropriate error handling" stand-ins.
