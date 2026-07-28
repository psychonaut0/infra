package tunnel

import (
	"os"
	"path/filepath"
	"strings"
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
	if !filepath.IsAbs(p) {
		t.Errorf("path not absolute: %q", p)
	}
	want := filepath.Join(".config", "infra", "cloudflare.yml")
	if !strings.HasSuffix(p, want) {
		t.Errorf("path = %q, want it to end in %q", p, want)
	}
}

func TestPermissionWarning_OKMode(t *testing.T) {
	p := writeCfg(t, fullCfg)
	if err := os.Chmod(p, 0o600); err != nil {
		t.Fatalf("chmod: %v", err)
	}
	if got := PermissionWarning(p); got != "" {
		t.Errorf("PermissionWarning(0600 file) = %q, want empty", got)
	}
}

func TestPermissionWarning_LooseMode(t *testing.T) {
	p := writeCfg(t, fullCfg)
	if err := os.Chmod(p, 0o644); err != nil {
		t.Fatalf("chmod: %v", err)
	}
	got := PermissionWarning(p)
	if got == "" {
		t.Fatal("PermissionWarning(0644 file) = \"\", want a non-empty warning")
	}
	if !strings.Contains(got, "644") {
		t.Errorf("PermissionWarning = %q, want it to mention the mode", got)
	}
}

func TestPermissionWarning_MissingFile(t *testing.T) {
	p := filepath.Join(t.TempDir(), "absent.yml")
	if got := PermissionWarning(p); got != "" {
		t.Errorf("PermissionWarning(missing file) = %q, want empty", got)
	}
}
