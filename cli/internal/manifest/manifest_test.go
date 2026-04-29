package manifest

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestParse_HappyPath(t *testing.T) {
	in := `{
		"version": "v0.4.0",
		"commit": "bd5e181",
		"published_at": "2026-04-29T12:34:56Z",
		"binaries": {
			"linux/amd64": {
				"url": "http://infra-bin.lan/linux/amd64/infra",
				"sha256": "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8"
			},
			"linux/arm64": {
				"url": "http://infra-bin.lan/linux/arm64/infra",
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
	if _, ok := m.Binaries["linux/arm64"]; !ok {
		t.Error("linux/arm64 missing")
	}
	if m.Commit != "bd5e181" {
		t.Errorf("Commit = %q", m.Commit)
	}
	if m.PublishedAt != "2026-04-29T12:34:56Z" {
		t.Errorf("PublishedAt = %q", m.PublishedAt)
	}
}

func TestParse_NoBinaries(t *testing.T) {
	in := `{"version":"v0.4.0","commit":"x","published_at":"2026-04-29T00:00:00Z","binaries":{}}`
	_, err := Parse(strings.NewReader(in))
	if err == nil {
		t.Fatal("expected error for manifest with no binaries")
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
