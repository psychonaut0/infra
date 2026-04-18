package discover

import (
	"errors"
	"testing"
)

func newTestIndex() *Index {
	return &Index{
		Services: map[string][]ServiceLocation{
			"sonarr":          {{CT: "ct-media"}},
			"portainer-agent": {{CT: "ct-dns"}, {CT: "ct-media"}},
		},
	}
}

func TestResolveUnique(t *testing.T) {
	idx := newTestIndex()
	loc, err := idx.Resolve("sonarr")
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	if loc.CT != "ct-media" {
		t.Errorf("want ct-media, got %s", loc.CT)
	}
}

func TestResolveExplicit(t *testing.T) {
	idx := newTestIndex()
	loc, err := idx.Resolve("ct-media:portainer-agent")
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	if loc.CT != "ct-media" {
		t.Errorf("want ct-media, got %s", loc.CT)
	}
}

func TestResolveAmbiguous(t *testing.T) {
	idx := newTestIndex()
	_, err := idx.Resolve("portainer-agent")
	if !errors.Is(err, ErrAmbiguous) {
		t.Fatalf("want ErrAmbiguous, got %v", err)
	}
}

func TestResolveNotFound(t *testing.T) {
	idx := newTestIndex()
	_, err := idx.Resolve("nope")
	if !errors.Is(err, ErrNotFound) {
		t.Fatalf("want ErrNotFound, got %v", err)
	}
}

func TestResolveExplicitNotFound(t *testing.T) {
	idx := newTestIndex()
	_, err := idx.Resolve("ct-dns:sonarr")
	if !errors.Is(err, ErrNotFound) {
		t.Fatalf("want ErrNotFound, got %v", err)
	}
}
