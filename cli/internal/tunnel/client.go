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
