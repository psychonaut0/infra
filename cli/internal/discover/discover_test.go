package discover

import (
	"path/filepath"
	"testing"
)

func TestWalk(t *testing.T) {
	// testdata is two dirs up from this package (cli/testdata/stacks)
	root := filepath.Join("..", "..", "testdata")
	idx, err := Walk(root)
	if err != nil {
		t.Fatalf("Walk: %v", err)
	}

	// Sanity: ct-dns has 2 services, ct-media has 3, totaling 5 — but
	// portainer-agent appears in both, so distinct names = 4, total entries = 5.
	if len(idx.All()) != 4 {
		t.Errorf("want 4 distinct service names, got %d: %v", len(idx.All()), idx.All())
	}

	// sonarr is only on ct-media
	got := idx.Services["sonarr"]
	if len(got) != 1 || got[0].CT != "ct-media" {
		t.Errorf("sonarr: want [{ct-media, …}], got %v", got)
	}

	// portainer-agent is on both
	pa := idx.Services["portainer-agent"]
	if len(pa) != 2 {
		t.Errorf("portainer-agent: want 2 entries, got %d: %v", len(pa), pa)
	}
}

func TestWalkMissingDir(t *testing.T) {
	_, err := Walk("/nonexistent/xyz/abc")
	if err == nil {
		t.Fatal("expected error for missing dir")
	}
}
