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

// configHelp returns the operator-facing hint shown when the local config is
// missing or incomplete.
func configHelp(path string) string {
	return fmt.Sprintf(`expected %s to contain:

  account_id: <cloudflare account id>
  tunnel_id: <tunnel id>
  public_domain: <your domain>
  api_token: <token>        # or set CF_API_TOKEN instead

Create the token at Cloudflare dashboard → My Profile → API Tokens with scope
exactly: Account → Cloudflare Tunnel → Read. No other permission is needed.`, path)
}

// LoadConfig reads and validates the operator-local config. CF_API_TOKEN, when
// set, overrides api_token from the file.
func LoadConfig(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w\n\n%s", path, err, configHelp(path))
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
		return nil, fmt.Errorf("%s is missing: %s\n\n%s",
			path, strings.Join(missing, ", "), configHelp(path))
	}

	return &cfg, nil
}

// PermissionWarning returns a human-readable warning if path's mode is more
// permissive than 0600, or "" when it is fine or cannot be determined.
// Returned rather than printed so that callers own presentation and this is
// testable.
func PermissionWarning(path string) string {
	info, err := os.Stat(path)
	if err != nil {
		return ""
	}
	if mode := info.Mode().Perm(); mode != 0o600 {
		return fmt.Sprintf("warning: %s has mode %#o, want 0600", path, mode)
	}
	return ""
}
