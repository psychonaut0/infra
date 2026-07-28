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

// --- Security-hardening tests: byte guard, case-insensitivity, domain
// normalisation, and nil-safety. See task-3-report.md "Fix pass" section for
// the findings these close.

func TestRender_RepeatedDomainSuffixTriggersByteGuard(t *testing.T) {
	// C1: strings.TrimSuffix used to strip only the trailing occurrence, so
	// "foo.example.test.example.test" rendered as
	// "foo.example.test.<PERSONAL_DOMAIN>" — the domain survived in the
	// retained prefix, and UnexpectedDomains reported nothing because the
	// hostname *did* match the domain suffix. The byte guard must now refuse
	// to return bytes at all.
	cfg := sample()
	cfg.Ingress = append(cfg.Ingress, Ingress{Hostname: "foo.example.test.example.test", Service: "http://x"})
	out, err := Render(cfg, "example.test")
	if err == nil {
		t.Fatalf("expected an error, got bytes:\n%s", out)
	}
	if out != nil {
		t.Errorf("expected no bytes on error, got:\n%s", out)
	}
	if !strings.Contains(err.Error(), "example.test") {
		t.Errorf("error should name the domain, got: %v", err)
	}
}

func TestRender_SubstitutionIsCaseInsensitive(t *testing.T) {
	// C3: DNS is not case-sensitive, so matching must not be either.
	cfg := &TunnelConfig{
		Source: "cloudflare",
		Ingress: []Ingress{
			{Hostname: "Portfolio.EXAMPLE.test", Service: "http://x"},
		},
	}
	out, err := Render(cfg, "example.test")
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	s := string(out)
	if strings.Contains(strings.ToLower(s), "example.test") {
		t.Errorf("real domain leaked into output:\n%s", s)
	}
	if !strings.Contains(s, "hostname: Portfolio."+DomainPlaceholder) {
		t.Errorf("expected substituted hostname preserving prefix case, got:\n%s", s)
	}
}

func TestRender_ConfiguredDomainUppercaseStillMatchesLowercaseHostname(t *testing.T) {
	cfg := &TunnelConfig{
		Source: "cloudflare",
		Ingress: []Ingress{
			{Hostname: "portfolio.example.test", Service: "http://x"},
		},
	}
	out, err := Render(cfg, "EXAMPLE.test")
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	s := string(out)
	if !strings.Contains(s, "hostname: portfolio."+DomainPlaceholder) {
		t.Errorf("expected substitution despite uppercase configured domain, got:\n%s", s)
	}
}

func TestRender_ApexHostnameEqualToDomain(t *testing.T) {
	// Zero coverage previously: the exact-match branch (hostname == domain,
	// no leading subdomain label to preserve).
	cfg := &TunnelConfig{
		Source: "cloudflare",
		Ingress: []Ingress{
			{Hostname: "example.test", Service: "http://x"},
		},
	}
	out, err := Render(cfg, "example.test")
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	s := string(out)
	if !strings.Contains(s, "hostname: "+DomainPlaceholder) {
		t.Errorf("expected bare apex hostname substituted, got:\n%s", s)
	}
	if strings.Contains(s, "hostname: example.test") {
		t.Errorf("real apex hostname leaked:\n%s", s)
	}
}

func TestRender_WildcardHostname(t *testing.T) {
	cfg := &TunnelConfig{
		Source: "cloudflare",
		Ingress: []Ingress{
			{Hostname: "*.example.test", Service: "http://x"},
		},
	}
	out, err := Render(cfg, "example.test")
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	s := string(out)
	// yaml.v3 quotes a scalar starting with '*' (YAML alias syntax), so match
	// loosely on the substituted value rather than the exact "hostname: " form.
	if !strings.Contains(s, "*."+DomainPlaceholder) {
		t.Errorf("expected wildcard hostname substituted, got:\n%s", s)
	}
	if strings.Contains(strings.ToLower(s), "example.test") {
		t.Errorf("real domain leaked into output:\n%s", s)
	}
}

func TestRender_DomainWithLeadingDotIsNormalised(t *testing.T) {
	cfg := &TunnelConfig{
		Source: "cloudflare",
		Ingress: []Ingress{
			{Hostname: "portfolio.example.test", Service: "http://x"},
		},
	}
	out, err := Render(cfg, ".example.test")
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	s := string(out)
	if !strings.Contains(s, "hostname: portfolio."+DomainPlaceholder) {
		t.Errorf("leading-dot domain should behave like example.test, got:\n%s", s)
	}
}

func TestRender_DomainWithSurroundingWhitespaceIsNormalised(t *testing.T) {
	cfg := &TunnelConfig{
		Source: "cloudflare",
		Ingress: []Ingress{
			{Hostname: "portfolio.example.test", Service: "http://x"},
		},
	}
	out, err := Render(cfg, " example.test ")
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	s := string(out)
	if !strings.Contains(s, "hostname: portfolio."+DomainPlaceholder) {
		t.Errorf("whitespace-padded domain should behave like example.test, got:\n%s", s)
	}
}

func TestRender_DomainOnlyInServiceTriggersByteGuard(t *testing.T) {
	// C2: the domain can hide in Service even when Hostname is clean.
	cfg := &TunnelConfig{
		Source: "cloudflare",
		Ingress: []Ingress{
			{Hostname: "portfolio." + "example.test", Service: "http://origin.example.test:3000"},
		},
	}
	out, err := Render(cfg, "example.test")
	if err == nil {
		t.Fatalf("expected an error, got bytes:\n%s", out)
	}
	if out != nil {
		t.Errorf("expected no bytes on error, got:\n%s", out)
	}
}

func TestRender_DomainOnlyInPathTriggersByteGuard(t *testing.T) {
	cfg := &TunnelConfig{
		Source: "cloudflare",
		Ingress: []Ingress{
			{Hostname: "portfolio.example.test", Path: "/example.test/api", Service: "http://x"},
		},
	}
	out, err := Render(cfg, "example.test")
	if err == nil {
		t.Fatalf("expected an error, got bytes:\n%s", out)
	}
	if out != nil {
		t.Errorf("expected no bytes on error, got:\n%s", out)
	}
}

func TestRender_DomainOnlyInOriginRequestTriggersByteGuard(t *testing.T) {
	cfg := &TunnelConfig{
		Source: "cloudflare",
		Ingress: []Ingress{
			{
				Hostname: "portfolio.example.test",
				Service:  "http://x",
				OriginRequest: map[string]any{
					"httpHostHeader": "origin.example.test",
				},
			},
		},
	}
	out, err := Render(cfg, "example.test")
	if err == nil {
		t.Fatalf("expected an error, got bytes:\n%s", out)
	}
	if out != nil {
		t.Errorf("expected no bytes on error, got:\n%s", out)
	}
}

func TestRender_NilConfigDoesNotPanic(t *testing.T) {
	out, err := Render(nil, "example.test")
	if err == nil {
		t.Fatal("expected an error for a nil config")
	}
	if out != nil {
		t.Errorf("expected no bytes, got:\n%s", out)
	}
}

func TestUnexpectedDomains_NilConfigDoesNotPanic(t *testing.T) {
	if got := UnexpectedDomains(nil, "example.test"); got != nil {
		t.Errorf("got %v, want nil", got)
	}
}
