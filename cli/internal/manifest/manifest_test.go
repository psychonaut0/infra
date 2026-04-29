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
