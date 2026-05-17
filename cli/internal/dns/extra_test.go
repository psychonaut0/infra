package dns

import (
	"path/filepath"
	"testing"
)

func TestExtra_RoundTrip(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "dns-extra.yaml")
	in := []ExtraEntry{
		{Name: "mc-vanilla.lan", IP: "192.168.3.14"},
		{Name: "mc-modded.lan", IP: "192.168.3.14"},
	}
	if err := WriteExtra(path, in); err != nil {
		t.Fatalf("WriteExtra: %v", err)
	}
	got, err := ReadExtra(path)
	if err != nil {
		t.Fatalf("ReadExtra: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("got %d entries, want 2", len(got))
	}
	// WriteExtra sorts alphabetically: mc-modded then mc-vanilla.
	if got[0].Name != "mc-modded.lan" || got[1].Name != "mc-vanilla.lan" {
		t.Errorf("sort order wrong: %+v", got)
	}
}

func TestExtra_Missing(t *testing.T) {
	got, err := ReadExtra(filepath.Join(t.TempDir(), "nope.yaml"))
	if err != nil {
		t.Fatalf("ReadExtra missing: %v", err)
	}
	if len(got) != 0 {
		t.Errorf("expected empty list, got %+v", got)
	}
}
