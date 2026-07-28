# `infra tunnel` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `infra tunnel` subcommand tree that mirrors the Cloudflare Tunnel's live ingress configuration into a version-controlled file, so the fleet's public routing is diffable, reviewable and recoverable from git.

**Architecture:** Three read-only subcommands (`ls`, `export`, `diff`) following the existing `infra dns` shape — cobra layer in `internal/cmd/tunnel.go` holding no logic, domain logic split across `internal/tunnel/{config,client,render,diff}.go` with unit tests. The Cloudflare API is read via a single GET; the client type exposes no mutating method. Rendering is deterministic so `diff` can compare bytes, and the real domain is substituted for `<PERSONAL_DOMAIN>` on the way out because the repo is public.

**Tech Stack:** Go 1.26, cobra 1.10.2, `gopkg.in/yaml.v3`, `github.com/jedib0t/go-pretty/v6/table` — all already in `cli/go.mod`. **No new dependencies.**

**Spec:** `docs/superpowers/specs/2026-07-28-infra-tunnel-export-design.md`

## Global Constraints

- **Read-only against Cloudflare.** The client type must expose no method that issues anything but `GET`. Required token scope is exactly `Account → Cloudflare Tunnel → Read`.
- **`github.com/psychonaut0/infra` is PUBLIC.** No tracked file may contain the real public domain, account ID, or tunnel ID. The placeholder is the literal string `<PERSONAL_DOMAIN>`.
- **`.gitignore` rules land before any code is written** (Task 1). The repo has no `*.token` or `*.json` credential rule today.
- **Operator-local config lives outside the repo tree** at `~/.config/infra/cloudflare.yml`, mode 0600.
- **Rendering must be deterministic.** `diff` compares bytes; any nondeterminism produces phantom drift. Verified prerequisite: `gopkg.in/yaml.v3` sorts `map[string]any` keys and is stable across runs.
- **Exit codes are a contract:** `diff` yields `0` identical, `2` drifted, `1` error. A scheduled caller must be able to distinguish drift from failure. Implemented as an exported `cmd.ErrDrift` sentinel mapped to `2` in `main()` — **not** `os.Exit` inside `RunE`, which would skip `PersistentPostRun` and make the command untestable. `internal/cmd` contains no `os.Exit`; keep it that way.
- **No new dependencies.** Everything needed is already in `go.mod`.
- Module path is `github.com/psychonaut0/infra/cli`. Package files carry doc comments on exported identifiers, matching `internal/dns/`.
- Tests use stdlib `testing` with `t.TempDir()` and `httptest` — **no live Cloudflare calls in the suite.**

---

## File Structure

**Create:**
- `cli/internal/tunnel/config.go` — operator-local config: parse, validate, resolve token.
- `cli/internal/tunnel/config_test.go`
- `cli/internal/tunnel/client.go` — Cloudflare API client. Read-only by construction.
- `cli/internal/tunnel/client_test.go`
- `cli/internal/tunnel/render.go` — `TunnelConfig` → deterministic, sanitised YAML.
- `cli/internal/tunnel/render_test.go`
- `cli/internal/tunnel/diff.go` — minimal line diff (no dependency available or wanted).
- `cli/internal/tunnel/diff_test.go`
- `cli/internal/cmd/tunnel.go` — cobra tree, flag wiring and output only.
- `stacks/ct-tunnel/README.md` — what this is, how to regenerate, why the tunnel stays remotely managed.
- `stacks/ct-tunnel/ingress.yml` — generated in Task 6, then committed.

**Modify:**
- `cli/internal/cmd/root.go` — register `newTunnelCmd()`.
- `.gitignore` — `*.token`, `**/credentials.json`.
- `CLAUDE.md` — `infra` task-table row; note ingress is now mirrored.
- `CLAUDE.local.md` — where the local config lives (gitignored, so real values are fine).

Responsibilities are separable: `config` knows nothing about HTTP, `client` knows nothing about YAML output, `render` knows nothing about files or the network, and `cmd` wires them together.

---

## Task 1: Gitignore rules and operator-local config

**Goal:** Close the secret-leak hole first, then add config loading. Nothing else can safely be written until `.gitignore` covers tokens.

**Files:**
- Modify: `.gitignore`
- Create: `cli/internal/tunnel/config.go`
- Test: `cli/internal/tunnel/config_test.go`

**Interfaces:**
- Produces: `tunnel.Config` struct with fields `AccountID, TunnelID, PublicDomain, APIToken string`; `tunnel.DefaultConfigPath() (string, error)`; `tunnel.LoadConfig(path string) (*Config, error)`.

- [ ] **Step 1: Add the gitignore rules**

Append to `.gitignore`:

```
# API tokens and tunnel credentials (never committed — this repo is public)
*.token
**/credentials.json
```

- [ ] **Step 2: Verify the rules bite**

```bash
cd /home/psy/Documents/personal/infra
touch /tmp/x && cp /tmp/x stacks/ct-tunnel/credentials.json && cp /tmp/x cli/test.token
git check-ignore -v stacks/ct-tunnel/credentials.json cli/test.token
rm -f stacks/ct-tunnel/credentials.json cli/test.token
```
Expected: both paths listed as ignored, each naming the rule that matched. If either prints nothing, the rule is wrong — fix before continuing.

- [ ] **Step 3: Commit the gitignore change on its own**

```bash
git add .gitignore
git commit -m "chore: gitignore API tokens and tunnel credentials

Lands before any infra tunnel code so a stray token or credentials.json cannot
be committed to a public repo. The existing rules cover **/.env, *.pem,
id_ed25519, **/secrets/ and *.local.md — none of which catch either of these."
```

- [ ] **Step 4: Write the failing test**

Create `cli/internal/tunnel/config_test.go`:

```go
package tunnel

import (
	"os"
	"path/filepath"
	"testing"
)

func writeCfg(t *testing.T, body string) string {
	t.Helper()
	dir := t.TempDir()
	p := filepath.Join(dir, "cloudflare.yml")
	if err := os.WriteFile(p, []byte(body), 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}
	return p
}

const fullCfg = `account_id: acct123
tunnel_id: tun456
public_domain: example.test
api_token: tok789
`

func TestLoadConfig_FromFile(t *testing.T) {
	t.Setenv("CF_API_TOKEN", "")
	got, err := LoadConfig(writeCfg(t, fullCfg))
	if err != nil {
		t.Fatalf("LoadConfig: %v", err)
	}
	if got.AccountID != "acct123" || got.TunnelID != "tun456" {
		t.Errorf("ids wrong: %+v", got)
	}
	if got.PublicDomain != "example.test" {
		t.Errorf("public_domain = %q, want example.test", got.PublicDomain)
	}
	if got.APIToken != "tok789" {
		t.Errorf("api_token = %q, want tok789", got.APIToken)
	}
}

func TestLoadConfig_EnvTokenWins(t *testing.T) {
	t.Setenv("CF_API_TOKEN", "from-env")
	got, err := LoadConfig(writeCfg(t, fullCfg))
	if err != nil {
		t.Fatalf("LoadConfig: %v", err)
	}
	if got.APIToken != "from-env" {
		t.Errorf("CF_API_TOKEN must win, got %q", got.APIToken)
	}
}

func TestLoadConfig_MissingFile(t *testing.T) {
	t.Setenv("CF_API_TOKEN", "")
	_, err := LoadConfig(filepath.Join(t.TempDir(), "absent.yml"))
	if err == nil {
		t.Fatal("expected an error for a missing config file")
	}
}

func TestLoadConfig_MissingFields(t *testing.T) {
	t.Setenv("CF_API_TOKEN", "")
	cases := map[string]string{
		"no account_id":    "tunnel_id: t\npublic_domain: d\napi_token: k\n",
		"no tunnel_id":     "account_id: a\npublic_domain: d\napi_token: k\n",
		"no public_domain": "account_id: a\ntunnel_id: t\napi_token: k\n",
		"no token at all":  "account_id: a\ntunnel_id: t\npublic_domain: d\n",
	}
	for name, body := range cases {
		if _, err := LoadConfig(writeCfg(t, body)); err == nil {
			t.Errorf("%s: expected an error", name)
		}
	}
}

func TestLoadConfig_TokenWhitespaceTrimmed(t *testing.T) {
	t.Setenv("CF_API_TOKEN", "  padded\n")
	got, err := LoadConfig(writeCfg(t, fullCfg))
	if err != nil {
		t.Fatalf("LoadConfig: %v", err)
	}
	if got.APIToken != "padded" {
		t.Errorf("token not trimmed: %q", got.APIToken)
	}
}

func TestDefaultConfigPath(t *testing.T) {
	p, err := DefaultConfigPath()
	if err != nil {
		t.Fatalf("DefaultConfigPath: %v", err)
	}
	want := filepath.Join(".config", "infra", "cloudflare.yml")
	if !filepath.IsAbs(p) {
		t.Errorf("path not absolute: %q", p)
	}
	if filepath.Join(filepath.Base(filepath.Dir(filepath.Dir(p))), filepath.Base(filepath.Dir(p)), filepath.Base(p)) != want {
		t.Errorf("path = %q, want it to end in %q", p, want)
	}
}
```

- [ ] **Step 5: Run the test to verify it fails**

```bash
cd /home/psy/Documents/personal/infra/cli && go test ./internal/tunnel/
```
Expected: FAIL — the package does not compile, `undefined: LoadConfig`, `undefined: DefaultConfigPath`.

- [ ] **Step 6: Write the implementation**

Create `cli/internal/tunnel/config.go`:

```go
// Package tunnel reads the Cloudflare Tunnel's live ingress configuration and
// renders it to a deterministic, sanitised file for version control.
//
// Everything here is read-only with respect to Cloudflare.
package tunnel

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
)

// DomainPlaceholder replaces the real public domain in rendered output. The
// infra repo is public, so the real domain must never reach a tracked file.
const DomainPlaceholder = "<PERSONAL_DOMAIN>"

// Config is the operator-local configuration for `infra tunnel`. It lives
// outside the repo tree (see DefaultConfigPath) precisely so that none of it
// can be committed.
type Config struct {
	AccountID    string `yaml:"account_id"`
	TunnelID     string `yaml:"tunnel_id"`
	PublicDomain string `yaml:"public_domain"`
	APIToken     string `yaml:"api_token"`
}

// DefaultConfigPath returns ~/.config/infra/cloudflare.yml.
func DefaultConfigPath() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("locate home directory: %w", err)
	}
	return filepath.Join(home, ".config", "infra", "cloudflare.yml"), nil
}

const configHelp = `expected %s to contain:

  account_id: <cloudflare account id>
  tunnel_id: <tunnel id>
  public_domain: <your domain>
  api_token: <token>        # or set CF_API_TOKEN instead

Create the token at Cloudflare dashboard → My Profile → API Tokens with scope
exactly: Account → Cloudflare Tunnel → Read. No other permission is needed.`

// LoadConfig reads and validates the operator-local config. CF_API_TOKEN, when
// set, overrides api_token from the file.
func LoadConfig(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w\n\n"+configHelp, path, err, path)
	}
	var cfg Config
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("parse %s: %w", path, err)
	}
	if env := strings.TrimSpace(os.Getenv("CF_API_TOKEN")); env != "" {
		cfg.APIToken = env
	}
	cfg.AccountID = strings.TrimSpace(cfg.AccountID)
	cfg.TunnelID = strings.TrimSpace(cfg.TunnelID)
	cfg.PublicDomain = strings.TrimSpace(cfg.PublicDomain)
	cfg.APIToken = strings.TrimSpace(cfg.APIToken)

	var missing []string
	if cfg.AccountID == "" {
		missing = append(missing, "account_id")
	}
	if cfg.TunnelID == "" {
		missing = append(missing, "tunnel_id")
	}
	// public_domain is required, not optional: without it the renderer cannot
	// sanitise hostnames and would write the real domain into a public repo.
	if cfg.PublicDomain == "" {
		missing = append(missing, "public_domain")
	}
	if cfg.APIToken == "" {
		missing = append(missing, "api_token (or CF_API_TOKEN)")
	}
	if len(missing) > 0 {
		return nil, fmt.Errorf("%s is missing: %s\n\n"+configHelp,
			path, strings.Join(missing, ", "), path)
	}

	if info, err := os.Stat(path); err == nil && info.Mode().Perm() != 0o600 {
		fmt.Fprintf(os.Stderr, "warning: %s has mode %#o, want 0600\n", path, info.Mode().Perm())
	}
	return &cfg, nil
}
```

- [ ] **Step 7: Run the test to verify it passes**

```bash
cd /home/psy/Documents/personal/infra/cli && go test ./internal/tunnel/ -v
```
Expected: PASS, 6 tests.

- [ ] **Step 8: Commit**

```bash
cd /home/psy/Documents/personal/infra
gofmt -w cli/
git add cli/internal/tunnel/config.go cli/internal/tunnel/config_test.go
git commit -m "feat(cli): infra tunnel operator-local config

Config lives at ~/.config/infra/cloudflare.yml, outside the repo tree, so
account/tunnel IDs and the API token cannot be committed to a public repo.

public_domain is required rather than optional: the renderer needs it to
substitute the real domain out, and without it export would leak."
```

---

## Task 2: Cloudflare API client

**Goal:** Fetch the live configuration over a single GET. Read-only by construction.

**Files:**
- Create: `cli/internal/tunnel/client.go`
- Test: `cli/internal/tunnel/client_test.go`

**Interfaces:**
- Consumes: `tunnel.Config` from Task 1.
- Produces: `tunnel.Ingress{Hostname, Path, Service string; OriginRequest map[string]any}`; `tunnel.TunnelConfig{Source string; Version int; Ingress []Ingress; WarpRouting, OriginRequest map[string]any}`; `tunnel.NewClient(cfg *Config) *Client`; `(*Client).WithBaseURL(string) *Client`; `(*Client).FetchConfig(context.Context) (*TunnelConfig, error)`.

- [ ] **Step 1: Write the failing test**

Create `cli/internal/tunnel/client_test.go`:

```go
package tunnel

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

const okBody = `{
  "success": true,
  "errors": [],
  "result": {
    "tunnel_id": "tun456",
    "version": 7,
    "source": "cloudflare",
    "config": {
      "ingress": [
        {"hostname": "portfolio.example.test", "service": "http://192.168.3.16:3000"},
        {"hostname": "drive.example.test", "service": "http://192.168.3.11:3923"},
        {"service": "http_status:404"}
      ]
    }
  }
}`

func testClient(t *testing.T, h http.Handler) (*Client, *httptest.Server) {
	t.Helper()
	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)
	cfg := &Config{AccountID: "acct123", TunnelID: "tun456", PublicDomain: "example.test", APIToken: "tok789"}
	return NewClient(cfg).WithBaseURL(srv.URL), srv
}

func TestFetchConfig_Success(t *testing.T) {
	var gotPath, gotAuth, gotMethod string
	c, _ := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath, gotAuth, gotMethod = r.URL.Path, r.Header.Get("Authorization"), r.Method
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(okBody))
	}))
	cfg, err := c.FetchConfig(context.Background())
	if err != nil {
		t.Fatalf("FetchConfig: %v", err)
	}
	if gotMethod != http.MethodGet {
		t.Errorf("method = %s, want GET", gotMethod)
	}
	want := "/accounts/acct123/cfd_tunnel/tun456/configurations"
	if gotPath != want {
		t.Errorf("path = %q, want %q", gotPath, want)
	}
	if gotAuth != "Bearer tok789" {
		t.Errorf("auth = %q", gotAuth)
	}
	if cfg.Source != "cloudflare" || cfg.Version != 7 {
		t.Errorf("source/version wrong: %+v", cfg)
	}
	if len(cfg.Ingress) != 3 {
		t.Fatalf("got %d ingress rules, want 3", len(cfg.Ingress))
	}
	if cfg.Ingress[0].Hostname != "portfolio.example.test" {
		t.Errorf("rule 0 hostname = %q", cfg.Ingress[0].Hostname)
	}
	if cfg.Ingress[2].Service != "http_status:404" || cfg.Ingress[2].Hostname != "" {
		t.Errorf("catch-all rule wrong: %+v", cfg.Ingress[2])
	}
}

func TestFetchConfig_OrderPreserved(t *testing.T) {
	c, _ := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(okBody))
	}))
	cfg, err := c.FetchConfig(context.Background())
	if err != nil {
		t.Fatalf("FetchConfig: %v", err)
	}
	// Ingress is first-match-wins, so order is semantic. Assert it verbatim.
	wantOrder := []string{"portfolio.example.test", "drive.example.test", ""}
	for i, w := range wantOrder {
		if cfg.Ingress[i].Hostname != w {
			t.Errorf("rule %d hostname = %q, want %q", i, cfg.Ingress[i].Hostname, w)
		}
	}
}

func TestFetchConfig_APIError(t *testing.T) {
	c, _ := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"success": false, "errors": [{"code": 1000, "message": "bad things"}], "result": null}`))
	}))
	_, err := c.FetchConfig(context.Background())
	if err == nil {
		t.Fatal("expected an error when success=false")
	}
	if !strings.Contains(err.Error(), "bad things") {
		t.Errorf("error should surface the API message, got %v", err)
	}
}

func TestFetchConfig_Forbidden(t *testing.T) {
	c, _ := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(`{"success": false, "errors": [{"code": 10000, "message": "Authentication error"}]}`))
	}))
	_, err := c.FetchConfig(context.Background())
	if err == nil {
		t.Fatal("expected an error on 403")
	}
	// A wrong-scope token is the most likely cause, so the message must say so
	// rather than dumping a raw status code.
	if !strings.Contains(err.Error(), "Cloudflare Tunnel") || !strings.Contains(err.Error(), "Read") {
		t.Errorf("403 message must name the required scope, got: %v", err)
	}
}

func TestFetchConfig_MalformedJSON(t *testing.T) {
	c, _ := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{not json`))
	}))
	if _, err := c.FetchConfig(context.Background()); err == nil {
		t.Fatal("expected an error on malformed JSON")
	}
}

func TestFetchConfig_EmptyIngress(t *testing.T) {
	c, _ := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"success":true,"result":{"version":1,"source":"cloudflare","config":{"ingress":[]}}}`))
	}))
	cfg, err := c.FetchConfig(context.Background())
	if err != nil {
		t.Fatalf("empty ingress should not error: %v", err)
	}
	if len(cfg.Ingress) != 0 {
		t.Errorf("want 0 rules, got %d", len(cfg.Ingress))
	}
}
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/psy/Documents/personal/infra/cli && go test ./internal/tunnel/
```
Expected: FAIL — `undefined: NewClient`, `undefined: Client`.

- [ ] **Step 3: Write the implementation**

Create `cli/internal/tunnel/client.go`:

```go
package tunnel

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// defaultBaseURL is the Cloudflare API v4 root.
const defaultBaseURL = "https://api.cloudflare.com/client/v4"

// Ingress is one hostname→service rule. Field order here determines the order
// keys are emitted in rendered YAML, so it is deliberate.
type Ingress struct {
	Hostname      string         `yaml:"hostname,omitempty"`
	Path          string         `yaml:"path,omitempty"`
	Service       string         `yaml:"service"`
	OriginRequest map[string]any `yaml:"originRequest,omitempty"`
}

// TunnelConfig is a tunnel's live configuration.
type TunnelConfig struct {
	Source        string         `yaml:"source"`
	Version       int            `yaml:"version"`
	Ingress       []Ingress      `yaml:"ingress"`
	WarpRouting   map[string]any `yaml:"warp-routing,omitempty"`
	OriginRequest map[string]any `yaml:"originRequest,omitempty"`
}

// Client fetches tunnel configuration from the Cloudflare API.
//
// It is read-only by construction: the only exported method issues a GET, and
// no method that mutates remote state exists. Keep it that way — the token this
// uses is scoped to Cloudflare Tunnel:Read and nothing more.
type Client struct {
	cfg     *Config
	baseURL string
	http    *http.Client
}

// NewClient returns a client for the tunnel described by cfg.
func NewClient(cfg *Config) *Client {
	return &Client{
		cfg:     cfg,
		baseURL: defaultBaseURL,
		http:    &http.Client{Timeout: 15 * time.Second},
	}
}

// WithBaseURL overrides the API root. Used by tests.
func (c *Client) WithBaseURL(u string) *Client {
	c.baseURL = strings.TrimRight(u, "/")
	return c
}

// apiEnvelope is the standard Cloudflare API response wrapper.
type apiEnvelope struct {
	Success bool `json:"success"`
	Errors  []struct {
		Code    int    `json:"code"`
		Message string `json:"message"`
	} `json:"errors"`
	Result struct {
		TunnelID string `json:"tunnel_id"`
		Version  int    `json:"version"`
		Source   string `json:"source"`
		Config   struct {
			Ingress []struct {
				Hostname      string         `json:"hostname"`
				Path          string         `json:"path"`
				Service       string         `json:"service"`
				OriginRequest map[string]any `json:"originRequest"`
			} `json:"ingress"`
			WarpRouting   map[string]any `json:"warp-routing"`
			OriginRequest map[string]any `json:"originRequest"`
		} `json:"config"`
	} `json:"result"`
}

const scopeHint = "the token needs scope Account → Cloudflare Tunnel → Read"

// FetchConfig retrieves the tunnel's current configuration.
func (c *Client) FetchConfig(ctx context.Context) (*TunnelConfig, error) {
	url := fmt.Sprintf("%s/accounts/%s/cfd_tunnel/%s/configurations",
		c.baseURL, c.cfg.AccountID, c.cfg.TunnelID)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, fmt.Errorf("build request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+c.cfg.APIToken)
	req.Header.Set("Accept", "application/json")

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("call Cloudflare API: %w", err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read response: %w", err)
	}

	if resp.StatusCode == http.StatusForbidden || resp.StatusCode == http.StatusUnauthorized {
		return nil, fmt.Errorf("Cloudflare returned %d — %s", resp.StatusCode, scopeHint)
	}

	var env apiEnvelope
	if err := json.Unmarshal(body, &env); err != nil {
		return nil, fmt.Errorf("parse response (HTTP %d): %w", resp.StatusCode, err)
	}
	if !env.Success {
		msgs := make([]string, 0, len(env.Errors))
		for _, e := range env.Errors {
			msgs = append(msgs, fmt.Sprintf("%d: %s", e.Code, e.Message))
		}
		if len(msgs) == 0 {
			msgs = append(msgs, fmt.Sprintf("HTTP %d with no error detail", resp.StatusCode))
		}
		return nil, fmt.Errorf("Cloudflare API error: %s", strings.Join(msgs, "; "))
	}

	out := &TunnelConfig{
		Source:        env.Result.Source,
		Version:       env.Result.Version,
		WarpRouting:   env.Result.Config.WarpRouting,
		OriginRequest: env.Result.Config.OriginRequest,
		Ingress:       make([]Ingress, 0, len(env.Result.Config.Ingress)),
	}
	for _, in := range env.Result.Config.Ingress {
		out.Ingress = append(out.Ingress, Ingress{
			Hostname:      in.Hostname,
			Path:          in.Path,
			Service:       in.Service,
			OriginRequest: in.OriginRequest,
		})
	}
	return out, nil
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /home/psy/Documents/personal/infra/cli && go test ./internal/tunnel/ -v -run TestFetchConfig
```
Expected: PASS, 6 `TestFetchConfig_*` tests.

- [ ] **Step 5: Confirm the read-only guarantee mechanically**

```bash
cd /home/psy/Documents/personal/infra/cli
grep -nE "http\.Method(Post|Put|Patch|Delete)|MethodPost|MethodPut|MethodPatch|MethodDelete" internal/tunnel/*.go || echo "read-only: no mutating HTTP methods present"
```
Expected: `read-only: no mutating HTTP methods present`.

- [ ] **Step 6: Commit**

```bash
cd /home/psy/Documents/personal/infra
gofmt -w cli/
git add cli/internal/tunnel/client.go cli/internal/tunnel/client_test.go
git commit -m "feat(cli): read-only Cloudflare tunnel config client

Single GET to /accounts/{a}/cfd_tunnel/{t}/configurations. The type exposes no
mutating method, matching the Cloudflare Tunnel:Read token scope.

A 403/401 reports the required scope rather than a bare status code, since a
wrong-scope token is the likeliest cause. Ingress order is preserved verbatim
because Cloudflare evaluates rules first-match-wins."
```

---

## Task 3: Deterministic, sanitised renderer

**Goal:** Turn a `TunnelConfig` into the exact bytes that get committed — with the real domain substituted out. This is the security-critical task.

**Files:**
- Create: `cli/internal/tunnel/render.go`
- Test: `cli/internal/tunnel/render_test.go`

**Interfaces:**
- Consumes: `tunnel.TunnelConfig`, `tunnel.Ingress`, `tunnel.DomainPlaceholder` from Tasks 1–2.
- Produces: `tunnel.Render(cfg *TunnelConfig, publicDomain string) ([]byte, error)`; `tunnel.UnexpectedDomains(cfg *TunnelConfig, publicDomain string) []string`.

- [ ] **Step 1: Write the failing test**

Create `cli/internal/tunnel/render_test.go`:

```go
package tunnel

import (
	"bytes"
	"strings"
	"testing"
)

func sample() *TunnelConfig {
	return &TunnelConfig{
		Source:  "cloudflare",
		Version: 7,
		Ingress: []Ingress{
			{Hostname: "portfolio.example.test", Service: "http://192.168.3.16:3000"},
			{Hostname: "drive.example.test", Service: "http://192.168.3.11:3923"},
			{Hostname: "family.example.test", Service: "http://192.168.3.11:3924"},
			{Service: "http_status:404"},
		},
	}
}

func TestRender_SubstitutesDomain(t *testing.T) {
	out, err := Render(sample(), "example.test")
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	s := string(out)
	if strings.Contains(s, "example.test") {
		t.Errorf("real domain leaked into output:\n%s", s)
	}
	for _, want := range []string{
		"hostname: portfolio." + DomainPlaceholder,
		"hostname: drive." + DomainPlaceholder,
		"hostname: family." + DomainPlaceholder,
		"service: http_status:404",
	} {
		if !strings.Contains(s, want) {
			t.Errorf("missing %q in:\n%s", want, s)
		}
	}
}

func TestRender_PreservesIngressOrder(t *testing.T) {
	out, err := Render(sample(), "example.test")
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	s := string(out)
	iPort := strings.Index(s, "portfolio.")
	iDrive := strings.Index(s, "drive.")
	iFam := strings.Index(s, "family.")
	iCatch := strings.Index(s, "http_status:404")
	if !(iPort < iDrive && iDrive < iFam && iFam < iCatch) {
		t.Errorf("ingress order not preserved (first-match-wins is semantic):\n%s", s)
	}
}

func TestRender_Deterministic(t *testing.T) {
	cfg := sample()
	cfg.Ingress[0].OriginRequest = map[string]any{
		"zebra": true, "alpha": 1, "middle": "x", "beta": 2, "yak": 3,
	}
	cfg.WarpRouting = map[string]any{"enabled": false}
	first, err := Render(cfg, "example.test")
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	// Go randomises map iteration, so an unsorted marshaller would diverge here.
	for i := 0; i < 25; i++ {
		again, err := Render(cfg, "example.test")
		if err != nil {
			t.Fatalf("Render #%d: %v", i, err)
		}
		if !bytes.Equal(first, again) {
			t.Fatalf("render #%d differs — diff would report phantom drift", i)
		}
	}
}

func TestRender_OmitsEmptyOptionalBlocks(t *testing.T) {
	out, err := Render(sample(), "example.test")
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	s := string(out)
	for _, absent := range []string{"originRequest", "warp-routing", "path:"} {
		if strings.Contains(s, absent) {
			t.Errorf("%q should be omitted when empty:\n%s", absent, s)
		}
	}
}

func TestRender_IncludesPathWhenSet(t *testing.T) {
	cfg := sample()
	cfg.Ingress[0].Path = "/api/*"
	out, err := Render(cfg, "example.test")
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	if !strings.Contains(string(out), "path: /api/*") {
		t.Errorf("path missing:\n%s", out)
	}
}

func TestRender_HasWarningHeader(t *testing.T) {
	out, err := Render(sample(), "example.test")
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	s := string(out)
	if !strings.HasPrefix(s, "#") {
		t.Error("output must start with a comment header")
	}
	for _, want := range []string{"DO NOT EDIT", "infra tunnel export"} {
		if !strings.Contains(s, want) {
			t.Errorf("header missing %q", want)
		}
	}
}

func TestRender_EmptyDomainIsAnError(t *testing.T) {
	// Rendering without a domain to substitute would write real hostnames into
	// a public repo, so it must fail loudly rather than silently pass through.
	if _, err := Render(sample(), ""); err == nil {
		t.Fatal("expected an error when publicDomain is empty")
	}
}

func TestRender_SourceLocalIsRecorded(t *testing.T) {
	cfg := sample()
	cfg.Source = "local"
	out, err := Render(cfg, "example.test")
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	if !strings.Contains(string(out), "source: local") {
		t.Errorf("source must be recorded verbatim:\n%s", out)
	}
}

func TestUnexpectedDomains(t *testing.T) {
	cfg := sample()
	cfg.Ingress = append(cfg.Ingress, Ingress{Hostname: "stray.other.test", Service: "http://x"})
	got := UnexpectedDomains(cfg, "example.test")
	if len(got) != 1 || got[0] != "stray.other.test" {
		t.Errorf("got %v, want [stray.other.test]", got)
	}
}

func TestUnexpectedDomains_NoneWhenAllMatch(t *testing.T) {
	if got := UnexpectedDomains(sample(), "example.test"); len(got) != 0 {
		t.Errorf("got %v, want none", got)
	}
}

func TestRender_UnexpectedDomainIsNotSubstituted(t *testing.T) {
	cfg := sample()
	cfg.Ingress = append(cfg.Ingress, Ingress{Hostname: "stray.other.test", Service: "http://x"})
	out, err := Render(cfg, "example.test")
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	// Left intact deliberately — UnexpectedDomains is what surfaces it. Silently
	// mangling an unknown domain would hide the problem.
	if !strings.Contains(string(out), "stray.other.test") {
		t.Errorf("unexpected domain should pass through unchanged:\n%s", out)
	}
}
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/psy/Documents/personal/infra/cli && go test ./internal/tunnel/ -run 'TestRender|TestUnexpected'
```
Expected: FAIL — `undefined: Render`, `undefined: UnexpectedDomains`.

- [ ] **Step 3: Write the implementation**

Create `cli/internal/tunnel/render.go`:

```go
package tunnel

import (
	"errors"
	"fmt"
	"strings"

	"gopkg.in/yaml.v3"
)

// ErrNoDomain is returned when rendering is attempted without a public domain
// to substitute. Rendering anyway would write real hostnames into a public repo.
var ErrNoDomain = errors.New("public_domain is empty: refusing to render, the real domain would be written verbatim")

const renderHeader = `# Generated by ` + "`infra tunnel export`" + ` — DO NOT EDIT BY HAND.
#
# This is a MIRROR of a remotely-managed Cloudflare tunnel. The Zero Trust
# dashboard is the source of truth; editing this file changes nothing there.
#
# Regenerate:      infra tunnel export
# Check for drift: infra tunnel diff
#
# The real domain is replaced with ` + DomainPlaceholder + ` because this repo is
# public. The real value lives in ~/.config/infra/cloudflare.yml and CLAUDE.local.md.
`

// Render serialises cfg to deterministic YAML with publicDomain replaced by
// DomainPlaceholder.
//
// Determinism matters: `infra tunnel diff` compares bytes, so any instability
// here shows up as phantom drift. Struct field order fixes key order, and
// yaml.v3 sorts map keys, which covers originRequest and warp-routing.
func Render(cfg *TunnelConfig, publicDomain string) ([]byte, error) {
	if strings.TrimSpace(publicDomain) == "" {
		return nil, ErrNoDomain
	}
	sanitised := *cfg
	sanitised.Ingress = make([]Ingress, len(cfg.Ingress))
	for i, in := range cfg.Ingress {
		in.Hostname = substituteDomain(in.Hostname, publicDomain)
		sanitised.Ingress[i] = in
	}
	body, err := yaml.Marshal(&sanitised)
	if err != nil {
		return nil, fmt.Errorf("marshal tunnel config: %w", err)
	}
	return append([]byte(renderHeader+"\n"), body...), nil
}

// substituteDomain replaces a trailing publicDomain with DomainPlaceholder.
// A hostname on any other domain is returned unchanged — UnexpectedDomains is
// what reports those, so that an unknown domain is surfaced rather than mangled.
func substituteDomain(hostname, publicDomain string) string {
	if hostname == "" {
		return ""
	}
	if hostname == publicDomain {
		return DomainPlaceholder
	}
	if strings.HasSuffix(hostname, "."+publicDomain) {
		return strings.TrimSuffix(hostname, publicDomain) + DomainPlaceholder
	}
	return hostname
}

// UnexpectedDomains returns hostnames that are not on publicDomain. These are
// written verbatim by Render, so callers must warn about them.
func UnexpectedDomains(cfg *TunnelConfig, publicDomain string) []string {
	var out []string
	for _, in := range cfg.Ingress {
		if in.Hostname == "" {
			continue // catch-all rule
		}
		if in.Hostname == publicDomain || strings.HasSuffix(in.Hostname, "."+publicDomain) {
			continue
		}
		out = append(out, in.Hostname)
	}
	return out
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /home/psy/Documents/personal/infra/cli && go test ./internal/tunnel/ -v -run 'TestRender|TestUnexpected'
```
Expected: PASS, 11 tests including `TestRender_Deterministic`.

- [ ] **Step 5: Commit**

```bash
cd /home/psy/Documents/personal/infra
gofmt -w cli/
git add cli/internal/tunnel/render.go cli/internal/tunnel/render_test.go
git commit -m "feat(cli): deterministic sanitised renderer for tunnel ingress

The real domain is substituted for <PERSONAL_DOMAIN> before writing, because the
repo is public. Rendering without a domain to substitute is a hard error rather
than a silent pass-through.

Determinism is load-bearing — diff compares bytes, so instability would report
phantom drift. Struct field order fixes key order and yaml.v3 sorts map keys;
the test renders 25 times and compares, which would catch an unsorted marshaller
given Go randomises map iteration.

Hostnames on an unexpected domain are left intact and reported by
UnexpectedDomains rather than mangled, so a surprise domain surfaces."
```

---

## Task 4: `infra tunnel ls`

**Goal:** First user-visible command. Fetch and print, write nothing.

**Files:**
- Create: `cli/internal/cmd/tunnel.go`
- Modify: `cli/internal/cmd/root.go`

**Interfaces:**
- Consumes: `tunnel.LoadConfig`, `tunnel.DefaultConfigPath`, `tunnel.NewClient`, `(*Client).FetchConfig`, `tunnel.UnexpectedDomains`.
- Produces: `newTunnelCmd() *cobra.Command`, registered on root.

- [ ] **Step 1: Write the command**

Create `cli/internal/cmd/tunnel.go`:

```go
package cmd

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/jedib0t/go-pretty/v6/table"
	"github.com/psychonaut0/infra/cli/internal/tunnel"
	"github.com/spf13/cobra"
)

// ingressRelPath is where the mirrored config lives inside the repo.
var ingressRelPath = filepath.Join("stacks", "ct-tunnel", "ingress.yml")

func newTunnelCmd() *cobra.Command {
	c := &cobra.Command{
		Use:   "tunnel",
		Short: "Mirror the Cloudflare Tunnel ingress config into the repo",
		Long: "tunnel reads the live Cloudflare Tunnel configuration (read-only) and\n" +
			"records it in stacks/ct-tunnel/ingress.yml so public routing is\n" +
			"version-controlled, diffable and recoverable from git history.\n\n" +
			"The Zero Trust dashboard remains the source of truth — these commands\n" +
			"only observe it. Requires ~/.config/infra/cloudflare.yml with a token\n" +
			"scoped to Account → Cloudflare Tunnel → Read.",
	}
	c.AddCommand(newTunnelLsCmd())
	return c
}

// loadTunnel resolves the local config and returns a client plus the config.
func loadTunnel() (*tunnel.Client, *tunnel.Config, error) {
	path, err := tunnel.DefaultConfigPath()
	if err != nil {
		return nil, nil, err
	}
	cfg, err := tunnel.LoadConfig(path)
	if err != nil {
		return nil, nil, err
	}
	return tunnel.NewClient(cfg), cfg, nil
}

// fetch pulls the live config with a bounded timeout.
func fetch(c *tunnel.Client) (*tunnel.TunnelConfig, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	return c.FetchConfig(ctx)
}

// warnUnexpected prints a warning for hostnames outside the configured domain,
// which Render passes through verbatim.
func warnUnexpected(live *tunnel.TunnelConfig, domain string) {
	if stray := tunnel.UnexpectedDomains(live, domain); len(stray) > 0 {
		fmt.Fprintf(os.Stderr,
			"warning: %d hostname(s) are not on the configured public_domain and will be written verbatim: %v\n",
			len(stray), stray)
	}
}

func newTunnelLsCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "ls",
		Short: "Show the live tunnel ingress rules",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			client, cfg, err := loadTunnel()
			if err != nil {
				return err
			}
			live, err := fetch(client)
			if err != nil {
				return err
			}
			t := table.NewWriter()
			t.SetOutputMirror(cmd.OutOrStdout())
			t.AppendHeader(table.Row{"#", "HOSTNAME", "PATH", "SERVICE"})
			for i, in := range live.Ingress {
				host := in.Hostname
				if host == "" {
					host = "(catch-all)"
				}
				t.AppendRow(table.Row{i + 1, host, in.Path, in.Service})
			}
			t.Render()
			fmt.Fprintf(cmd.OutOrStdout(), "\nsource: %s   version: %d   rules: %d\n",
				live.Source, live.Version, len(live.Ingress))
			if live.Source != "cloudflare" {
				fmt.Fprintf(os.Stderr,
					"warning: source is %q, not \"cloudflare\" — this tunnel's management mode changed\n",
					live.Source)
			}
			warnUnexpected(live, cfg.PublicDomain)
			return nil
		},
	}
}
```

- [ ] **Step 2: Register the command**

In `cli/internal/cmd/root.go`, add after the `newDnsCmd()` line:

```go
	root.AddCommand(newTunnelCmd())
```

- [ ] **Step 3: Build and confirm the command tree**

```bash
cd /home/psy/Documents/personal/infra/cli
go build ./... && go vet ./... && go run ./cmd/infra tunnel --help
```
Expected: builds clean, vet clean, and the help text lists `ls`.

- [ ] **Step 4: Confirm it fails helpfully with no config**

```bash
cd /home/psy/Documents/personal/infra/cli
HOME=$(mktemp -d) go run ./cmd/infra tunnel ls; echo "exit=$?"
```
Expected: exit=1 with a message naming `cloudflare.yml`, showing the expected shape, and stating the required token scope. **Not** a Go panic or a bare "no such file".

- [ ] **Step 5: Run the whole suite**

```bash
cd /home/psy/Documents/personal/infra/cli && go test ./...
```
Expected: all packages PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/psy/Documents/personal/infra
gofmt -w cli/
git add cli/internal/cmd/tunnel.go cli/internal/cmd/root.go
git commit -m "feat(cli): add infra tunnel ls

Prints the live ingress rules read-only. Warns when source is not 'cloudflare'
(the management mode changed) and when a hostname sits outside the configured
public_domain, since the renderer passes those through verbatim."
```

---

## Task 5: `infra tunnel export` and `infra tunnel diff`

**Goal:** Write the mirrored file, and detect drift with a distinguishable exit code.

**Files:**
- Create: `cli/internal/tunnel/diff.go`
- Test: `cli/internal/tunnel/diff_test.go`
- Modify: `cli/internal/cmd/tunnel.go`
- Modify: `cli/cmd/infra/main.go` — map the drift sentinel to exit 2

**Interfaces:**
- Consumes: `tunnel.Render`, `tunnel.UnexpectedDomains`, `repo.Locate`.
- Produces: `tunnel.UnifiedDiff(want, got []byte, wantName, gotName string) string`; `cmd.ErrDrift` (exported sentinel); cobra subcommands `export` and `diff`.

- [ ] **Step 1: Write the failing diff test**

Create `cli/internal/tunnel/diff_test.go`:

```go
package tunnel

import (
	"strings"
	"testing"
)

func TestUnifiedDiff_Identical(t *testing.T) {
	b := []byte("a\nb\nc\n")
	if d := UnifiedDiff(b, b, "repo", "live"); d != "" {
		t.Errorf("identical input must produce an empty diff, got:\n%s", d)
	}
}

func TestUnifiedDiff_ShowsAddedAndRemoved(t *testing.T) {
	repoSide := []byte("keep\nold\ntail\n")
	liveSide := []byte("keep\nnew\ntail\n")
	d := UnifiedDiff(repoSide, liveSide, "repo", "live")
	if !strings.Contains(d, "-old") {
		t.Errorf("missing removed line marker:\n%s", d)
	}
	if !strings.Contains(d, "+new") {
		t.Errorf("missing added line marker:\n%s", d)
	}
	if !strings.Contains(d, "repo") || !strings.Contains(d, "live") {
		t.Errorf("diff should label both sides:\n%s", d)
	}
}

func TestUnifiedDiff_HandlesLengthChange(t *testing.T) {
	d := UnifiedDiff([]byte("one\n"), []byte("one\ntwo\nthree\n"), "repo", "live")
	if !strings.Contains(d, "+two") || !strings.Contains(d, "+three") {
		t.Errorf("added trailing lines missing:\n%s", d)
	}
}

func TestUnifiedDiff_HandlesEmptySide(t *testing.T) {
	d := UnifiedDiff(nil, []byte("only\n"), "repo", "live")
	if !strings.Contains(d, "+only") {
		t.Errorf("expected the live line as an addition:\n%s", d)
	}
}
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/psy/Documents/personal/infra/cli && go test ./internal/tunnel/ -run TestUnifiedDiff
```
Expected: FAIL — `undefined: UnifiedDiff`.

- [ ] **Step 3: Write the diff implementation**

Create `cli/internal/tunnel/diff.go`:

```go
package tunnel

import (
	"fmt"
	"strings"
)

// UnifiedDiff returns a human-readable line diff of want vs got, or "" when
// they are byte-identical.
//
// This is deliberately a simple line-by-line comparison rather than a real LCS
// diff: the inputs are two renderings of the same deterministic template, so
// differences are small and positional. It also avoids adding a dependency.
func UnifiedDiff(want, got []byte, wantName, gotName string) string {
	if string(want) == string(got) {
		return ""
	}
	wantLines := splitLines(want)
	gotLines := splitLines(got)

	var b strings.Builder
	fmt.Fprintf(&b, "--- %s\n+++ %s\n", wantName, gotName)
	n := len(wantLines)
	if len(gotLines) > n {
		n = len(gotLines)
	}
	for i := 0; i < n; i++ {
		var w, g string
		var haveW, haveG bool
		if i < len(wantLines) {
			w, haveW = wantLines[i], true
		}
		if i < len(gotLines) {
			g, haveG = gotLines[i], true
		}
		switch {
		case haveW && haveG && w == g:
			// unchanged: show as context
			fmt.Fprintf(&b, " %s\n", w)
		default:
			if haveW {
				fmt.Fprintf(&b, "-%s\n", w)
			}
			if haveG {
				fmt.Fprintf(&b, "+%s\n", g)
			}
		}
	}
	return b.String()
}

func splitLines(b []byte) []string {
	if len(b) == 0 {
		return nil
	}
	s := strings.TrimSuffix(string(b), "\n")
	if s == "" {
		return nil
	}
	return strings.Split(s, "\n")
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /home/psy/Documents/personal/infra/cli && go test ./internal/tunnel/ -v -run TestUnifiedDiff
```
Expected: PASS, 4 tests.

- [ ] **Step 5: Add the two subcommands**

In `cli/internal/cmd/tunnel.go`, add two imports that Task 4 did not need — `"errors"` and `"github.com/psychonaut0/infra/cli/internal/repo"` (Go fails to compile on an unused import, which is why they were left out until now) — then add to `newTunnelCmd()`:

```go
	c.AddCommand(newTunnelExportCmd())
	c.AddCommand(newTunnelDiffCmd())
```

Then append:

```go
// ErrDrift is returned by `infra tunnel diff` when the repo file and the live
// config differ. main() maps it to exit code 2, which is distinct from 1
// (error) so a scheduled caller can tell drift from a failed check — treating
// an API outage as drift would cry wolf.
//
// Returned as a sentinel rather than calling os.Exit here, so that cobra's
// PersistentPostRun still runs and the command stays testable. internal/cmd
// contains no os.Exit by design.
var ErrDrift = errors.New("drift detected between the repo and the live tunnel config")

// renderLive fetches the live config and renders it to the bytes that belong in
// the repo. Shared by export and diff so the two can never disagree.
func renderLive() (rendered []byte, path string, err error) {
	client, cfg, err := loadTunnel()
	if err != nil {
		return nil, "", err
	}
	live, err := fetch(client)
	if err != nil {
		return nil, "", err
	}
	warnUnexpected(live, cfg.PublicDomain)
	out, err := tunnel.Render(live, cfg.PublicDomain)
	if err != nil {
		return nil, "", err
	}
	root, err := repo.Locate()
	if err != nil {
		return nil, "", err
	}
	return out, filepath.Join(root, ingressRelPath), nil
}

func newTunnelExportCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "export",
		Short: "Write the live ingress config to stacks/ct-tunnel/ingress.yml",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			out, path, err := renderLive()
			if err != nil {
				return err
			}
			prev, readErr := os.ReadFile(path)
			unchanged := readErr == nil && string(prev) == string(out)

			if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
				return fmt.Errorf("create %s: %w", filepath.Dir(path), err)
			}
			if err := os.WriteFile(path, out, 0o644); err != nil {
				return fmt.Errorf("write %s: %w", path, err)
			}
			if unchanged {
				fmt.Fprintf(cmd.OutOrStdout(), "%s (no change)\n", path)
			} else {
				fmt.Fprintf(cmd.OutOrStdout(), "%s (updated — review and commit)\n", path)
			}
			return nil
		},
	}
}

func newTunnelDiffCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "diff",
		Short: "Report drift between the committed ingress.yml and live config",
		Long: "diff exits 0 when the repo matches live, 2 when they differ, and 1 on\n" +
			"error. The distinct drift code lets a scheduled caller tell real drift\n" +
			"from a failed check.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			out, path, err := renderLive()
			if err != nil {
				return err
			}
			repoBytes, err := os.ReadFile(path)
			if err != nil && !errors.Is(err, os.ErrNotExist) {
				return fmt.Errorf("read %s: %w", path, err)
			}
			d := tunnel.UnifiedDiff(repoBytes, out, path+" (repo)", "live")
			if d == "" {
				fmt.Fprintf(cmd.OutOrStdout(), "in sync: %s matches live\n", path)
				return nil
			}
			fmt.Fprint(cmd.OutOrStdout(), d)
			fmt.Fprintf(cmd.OutOrStdout(), "\nrun `infra tunnel export` to update the repo\n")
			return ErrDrift
		},
	}
}
```

- [ ] **Step 5a: Map the drift sentinel to exit code 2**

Replace the body of `main()` in `cli/cmd/infra/main.go`:

```go
func main() {
	err := cmd.Execute(cmd.BuildInfo{Version: Version, Commit: Commit})
	if err == nil {
		return
	}
	// `infra tunnel diff` signals drift with a distinct exit code so a
	// scheduled caller can tell "drifted" from "the check itself failed".
	if errors.Is(err, cmd.ErrDrift) {
		os.Exit(2)
	}
	os.Exit(1)
}
```

Add `"errors"` to that file's imports.

- [ ] **Step 5b: Confirm the mapping compiles and the sentinel is reachable**

```bash
cd /home/psy/Documents/personal/infra/cli
go build ./... && go vet ./...
grep -n "ErrDrift" internal/cmd/tunnel.go cmd/infra/main.go
```
Expected: builds and vets clean; `ErrDrift` appears as a declaration in
`internal/cmd/tunnel.go` and in an `errors.Is` check in `cmd/infra/main.go`.

- [ ] **Step 6: Build, vet, and run the full suite**

```bash
cd /home/psy/Documents/personal/infra/cli
go build ./... && go vet ./... && go test ./...
```
Expected: builds clean, vet clean, all tests PASS.

- [ ] **Step 7: Commit**

```bash
cd /home/psy/Documents/personal/infra
gofmt -w cli/
git add cli/internal/tunnel/diff.go cli/internal/tunnel/diff_test.go cli/internal/cmd/tunnel.go cli/cmd/infra/main.go
git commit -m "feat(cli): add infra tunnel export and diff

export and diff share one renderLive() helper so they can never disagree about
what belongs in the repo.

diff signals drift with an exported ErrDrift sentinel that main() maps to exit
2, leaving 1 for real errors. The distinction matters for a future scheduled
check: treating an API outage as drift would cry wolf. A sentinel rather than
os.Exit keeps PersistentPostRun running and the command testable — internal/cmd
has no os.Exit by design.

UnifiedDiff is a deliberately simple positional line diff — both sides are
renderings of the same deterministic template, and it avoids a new dependency."
```

---

## Task 6: Provision the token and export for real

**Goal:** Run it against the live tunnel and commit the mirrored file. This is the task where the project's goal is actually met.

**Files:**
- Create: `stacks/ct-tunnel/ingress.yml` (generated)
- Modify: `CLAUDE.local.md`

- [ ] **Step 1: Create the API token**

In the Cloudflare dashboard: **My Profile → API Tokens → Create Token → Create Custom Token**.

- Permissions: **Account** → **Cloudflare Tunnel** → **Read**. Add nothing else.
- Account Resources: include the one account.
- No Zone resources needed.

Copy the token once — Cloudflare does not show it again.

- [ ] **Step 2: Write the local config**

```bash
mkdir -p ~/.config/infra
cat > ~/.config/infra/cloudflare.yml <<'EOF'
account_id: <paste account id>
tunnel_id: <paste tunnel id>
public_domain: <paste real domain>
api_token: <paste token>
EOF
chmod 600 ~/.config/infra/cloudflare.yml
ls -l ~/.config/infra/cloudflare.yml
```
Expected: mode `-rw-------`. The account ID can be read from the tunnel token with:
```bash
ssh ct-tunnel 'grep -oE "TUNNEL_TOKEN=[A-Za-z0-9+/=]+" /opt/stacks/ct-tunnel/.env | cut -d= -f2- | base64 -d' | python3 -c 'import json,sys; print(json.load(sys.stdin)["a"])'
```

- [ ] **Step 3: Install and run `ls`**

```bash
cd /home/psy/Documents/personal/infra/cli && make install
infra tunnel ls
```
Expected: a table with the three known hostnames (`portfolio`, `drive`, `family`) plus a `(catch-all)` row, and `source: cloudflare`. Cross-check hostname→origin against the dashboard's Public Hostnames page — they must match exactly.

- [ ] **Step 4: Confirm a wrong-scope token is diagnosed**

```bash
CF_API_TOKEN=definitely-not-a-real-token infra tunnel ls; echo "exit=$?"
```
Expected: exit=1 with a message naming `Account → Cloudflare Tunnel → Read`, not a bare HTTP status.

- [ ] **Step 5: Export**

```bash
cd /home/psy/Documents/personal/infra
infra tunnel export
cat stacks/ct-tunnel/ingress.yml
```
Expected: the file lists the three hostnames as `drive.<PERSONAL_DOMAIN>` etc., plus the catch-all, `source: cloudflare`, a version number, and the DO-NOT-EDIT header.

- [ ] **Step 6: Verify no leak, and that export is idempotent**

```bash
cd /home/psy/Documents/personal/infra
D=$(python3 -c "import yaml;print(yaml.safe_load(open('$HOME/.config/infra/cloudflare.yml'))['public_domain'])")
grep -c "$D" stacks/ct-tunnel/ingress.yml
A=$(python3 -c "import yaml;print(yaml.safe_load(open('$HOME/.config/infra/cloudflare.yml'))['account_id'])")
grep -c "$A\|$T" stacks/ct-tunnel/ingress.yml
sha256sum stacks/ct-tunnel/ingress.yml
infra tunnel export
sha256sum stacks/ct-tunnel/ingress.yml
```
Expected: both `grep -c` print **0**; both sha256 sums identical; the second export reports `(no change)`. **If the domain count is not 0, stop — do not commit.**

- [ ] **Step 7: Verify `diff` in both directions**

```bash
cd /home/psy/Documents/personal/infra
infra tunnel diff; echo "in-sync exit=$?"
printf '\n# deliberate local edit\n' >> stacks/ct-tunnel/ingress.yml
infra tunnel diff; echo "drift exit=$?"
infra tunnel export
infra tunnel diff; echo "restored exit=$?"
CF_API_TOKEN=bogus infra tunnel diff; echo "error exit=$?"
```
Expected: `in-sync exit=0`, `drift exit=2` with a diff shown, `restored exit=0`, `error exit=1`. The 2-vs-1 distinction is the contract.

- [ ] **Step 7a: Verify drift is detected from a real dashboard change**

A hand-edited file proves the comparison works; it does not prove the *live* side
is actually re-fetched. In the Cloudflare dashboard, add a throwaway public
hostname (e.g. `drift-test` → `http://192.168.3.11:3923`), then:

```bash
cd /home/psy/Documents/personal/infra
infra tunnel diff; echo "exit=$?"
```
Expected: `exit=2`, and the diff shows a `+` line for `drift-test.<PERSONAL_DOMAIN>`
and a changed `version:`.

Now remove that hostname in the dashboard and re-check:
```bash
infra tunnel diff; echo "exit=$?"
```
Expected: back to `exit=0` — except for `version:`, which Cloudflare increments on
every config change and will now be two higher than the committed file. That is a
real, expected drift signal (it means someone edited and reverted). Resolve it:
```bash
infra tunnel export && git diff --stat stacks/ct-tunnel/ingress.yml
```
Expected: only the `version:` line changed. Commit it with the others in Step 9.

- [ ] **Step 8: Record the config location in `CLAUDE.local.md`**

Append to `CLAUDE.local.md` (gitignored, so real values are fine here):

```markdown
## `infra tunnel` local config

`~/.config/infra/cloudflare.yml` (0600) — holds `account_id`, `tunnel_id`,
`public_domain` and a Cloudflare API token scoped to
**Account → Cloudflare Tunnel → Read** (read-only; it cannot modify the tunnel).

Needed by `infra tunnel ls|export|diff`. It lives outside the repo tree on
purpose: the repo is public, so account/tunnel IDs and the real domain must not
be committed. `infra tunnel export` substitutes the domain for
`<PERSONAL_DOMAIN>` in `stacks/ct-tunnel/ingress.yml`.
```

- [ ] **Step 9: Commit the mirrored config**

```bash
cd /home/psy/Documents/personal/infra
git add stacks/ct-tunnel/ingress.yml
git status --porcelain   # CLAUDE.local.md must NOT appear — it is gitignored
git commit -m "feat(ct-tunnel): mirror tunnel ingress config into the repo

Public routing is now version-controlled and pushed to GitHub, instead of
existing only in the Cloudflare Zero Trust dashboard. Note the off-site copy is
GitHub, not restic — ct-backup pulls from CTs and the Proxmox nodes, not from a
workstation checkout.

Generated by 'infra tunnel export' with the real domain substituted for
<PERSONAL_DOMAIN>. Verified: zero occurrences of the real domain, account ID or
tunnel ID in the committed file, and export is idempotent."
```

---

## Task 7: Documentation and release

**Goal:** Make the command discoverable, and ship it to the fleet.

**Files:**
- Create: `stacks/ct-tunnel/README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Write the ct-tunnel README**

Create `stacks/ct-tunnel/README.md`:

```markdown
# ct-tunnel

Cloudflare Tunnel endpoint. Runs `cloudflared` for selective public access to
internal services.

## This tunnel is remotely managed

`cloudflared tunnel run` with a `TUNNEL_TOKEN` — ingress rules live in the
Cloudflare Zero Trust dashboard, not on disk. Adding or changing a public
hostname is a **dashboard action that produces no diff in this repo.**

`stacks/ct-tunnel/ingress.yml` is a **mirror** of that state, written by
`infra tunnel export`. It is not the source of truth and editing it changes
nothing.

| Command | Does |
|---|---|
| `infra tunnel ls` | Print the live ingress rules |
| `infra tunnel export` | Refresh `ingress.yml` (then commit it) |
| `infra tunnel diff` | Report drift — exit 0 in sync, 2 drifted, 1 error |

All three are read-only against Cloudflare. They need
`~/.config/infra/cloudflare.yml` with a token scoped to
**Account → Cloudflare Tunnel → Read**; see `CLAUDE.local.md`.

**After changing a public hostname in the dashboard, run `infra tunnel export`
and commit** — otherwise the mirror goes stale silently.

## Why not a locally-managed tunnel

A tunnel's management mode (`config_src`) is fixed at creation. `PATCH` accepts
only `name` and `tunnel_secret`, and Cloudflare's Terraform provider marks
`config_src` `RequiresReplaceIfConfigured()`. The maintainer confirms it in
cloudflared#1029: *"we don't allow tunnels to be migrated to 'locally
managed'"*. Converting would mean a new tunnel UUID and repointing every CNAME,
including the portfolio — and would not even prevent drift, since the dashboard
Configure action stays available.

Note: the `TUNNEL_TOKEN` decodes to `{a, s, t}` = `AccountTag` / `TunnelSecret` /
`TunnelID`, so a valid `credentials.json` *can* be built from it — but the edge
replies `TunnelIsRemotelyManaged` and pushes config that overrides any local
ingress, so it achieves nothing.

Full reasoning: `docs/superpowers/specs/2026-07-28-infra-tunnel-export-design.md`

## Known gaps

- **No automatic drift alerting.** `diff` must be run. Its exit codes are
  designed for a future timer.
- The dashboard remains authoritative — this records and detects, it does not
  prevent.
- One tunnel, one account, one domain. A second domain's hostnames would be
  written verbatim and reported as unexpected.
```

- [ ] **Step 2: Add the CLI table row in `CLAUDE.md`**

In the `infra` task table, after the `Audit DNS/Caddy drift` row, add:

```markdown
| Mirror + audit Cloudflare Tunnel ingress                    | `infra tunnel ls`, `infra tunnel export`, `infra tunnel diff` |
```

- [ ] **Step 3: Update the ct-tunnel section in `CLAUDE.md`**

Append to the ct-tunnel **Config notes**:

```
Ingress rules are **remotely managed** (dashboard-only) and mirrored into `stacks/ct-tunnel/ingress.yml` by `infra tunnel export` — run it after any dashboard change or the mirror goes stale. `infra tunnel diff` audits drift (exit 2 = drifted). Read-only; needs a `Cloudflare Tunnel:Read` token per `CLAUDE.local.md`. Runbook: `stacks/ct-tunnel/README.md`.
```

- [ ] **Step 4: Update the "Tunnel ingress is NOT version-controlled" note in `CLAUDE.md`**

That bullet in *Upstream / External Access* is now out of date. Replace it with:

```markdown
- **Tunnel ingress is remotely managed but mirrored.** ct-tunnel runs `cloudflared tunnel run` with a `TUNNEL_TOKEN`, so hostname→origin rules live in the Cloudflare Zero Trust dashboard and a change there produces no repo diff. `stacks/ct-tunnel/ingress.yml` mirrors that state via `infra tunnel export`; `infra tunnel diff` reports drift. Run export after any dashboard change.
```

- [ ] **Step 5: Verify docs match reality**

```bash
cd /home/psy/Documents/personal/infra
infra tunnel diff; echo "exit=$?"
grep -c "infra tunnel" CLAUDE.md stacks/ct-tunnel/README.md
git grep -c "NOT version-controlled" CLAUDE.md || echo "stale note removed"
```
Expected: `exit=0`; both files reference `infra tunnel`; the stale note is gone.

- [ ] **Step 6: Commit**

```bash
cd /home/psy/Documents/personal/infra
git add CLAUDE.md stacks/ct-tunnel/README.md
git commit -m "docs(ct-tunnel): document infra tunnel and the mirrored ingress

Records why the tunnel stays remotely managed (config_src is fixed at creation;
converting needs a new UUID and every CNAME repointed, and would not prevent
drift anyway), and that ingress.yml is a mirror rather than a source of truth.

Replaces the now-stale 'tunnel ingress is NOT version-controlled' note."
```

- [ ] **Step 7: Tag and release**

```bash
cd /home/psy/Documents/personal/infra
git tag v0.7.0
git push origin master
git push origin v0.7.0
gh run watch
```
Expected: the release workflow builds and publishes artifacts. **Note:** this is the first push of this branch — it publishes every local commit to a public repo. Confirm the sanitization audit passes first:
```bash
git grep -nE "$(python3 -c "import yaml;print(yaml.safe_load(open('$HOME/.config/infra/cloudflare.yml'))['public_domain'].replace('.','\\\\.'))")" -- . || echo "no domain leak in tracked files"
```

- [ ] **Step 8: Roll out to the fleet**

```bash
infra update -y
infra version
```
Expected: `infra v0.7.0`. Then spot-check one CT:
```bash
ssh ct-mgmt 'infra version'
```
Expected: `v0.7.0`. Note `infra tunnel` will not work there without the local config — that is by design; it is an operator-host command.

---

## Done criteria

- [ ] `.gitignore` covers `*.token` and `**/credentials.json`, verified with `git check-ignore`.
- [ ] `go test ./...` passes; `go vet ./...` clean; `gofmt` clean.
- [ ] No mutating HTTP method exists anywhere in `internal/tunnel/`.
- [ ] `infra tunnel ls` matches the dashboard's Public Hostnames exactly.
- [ ] A wrong-scope token produces a message naming the required scope, exit 1.
- [ ] `stacks/ct-tunnel/ingress.yml` is committed and contains **zero** occurrences of the real domain, account ID, or tunnel ID.
- [ ] `infra tunnel export` is idempotent (identical sha256 across two runs).
- [ ] `infra tunnel diff` exits **0** in sync, **2** on drift, **1** on error — all three observed.
- [ ] `CLAUDE.local.md` records the local config location; it is gitignored and unstaged.
- [ ] `CLAUDE.md`'s stale "NOT version-controlled" note is replaced.
- [ ] `v0.7.0` released and `infra update` rolled out.

## Deferred (recorded, not built)

- **Scheduled drift check with Telegram alerting.** `diff`'s exit codes are the contract a timer needs. Natural home is a systemd timer on ct-mgmt beside `infra-mirror.timer`, but that host has no repo checkout, so it is real work rather than a one-liner.
- **A `--check` mode for CI**, if this repo ever gains CI that asserts no-drift.
- **Second tunnel / second domain support.**
- Revisit the locally-managed migration only if Cloudflare changes its stance on `config_src`, or if detection alone proves insufficient.
