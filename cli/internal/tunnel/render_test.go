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
