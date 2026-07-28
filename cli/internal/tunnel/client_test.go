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
	// A 403 doesn't only mean a wrong-scope token — it can also mean an expired
	// token, a wrong account ID, or an IP allow-list rule. Cloudflare's own
	// error detail must survive alongside the scope hint so the operator isn't
	// steered toward the wrong fix.
	if !strings.Contains(err.Error(), "10000") || !strings.Contains(err.Error(), "Authentication error") {
		t.Errorf("403 message must also surface Cloudflare's own error detail, got: %v", err)
	}
}

func TestFetchConfig_ForbiddenWithoutParseableBody(t *testing.T) {
	c, _ := testClient(t, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(`not json at all`))
	}))
	_, err := c.FetchConfig(context.Background())
	if err == nil {
		t.Fatal("expected an error on 403")
	}
	// No parseable error detail in the body: fall back to the hint-only
	// message so this path is never worse than before.
	if !strings.Contains(err.Error(), "Cloudflare Tunnel") || !strings.Contains(err.Error(), "Read") {
		t.Errorf("403 message must still name the required scope, got: %v", err)
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
