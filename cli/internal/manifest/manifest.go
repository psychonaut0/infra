// Package manifest defines the schema and a parser for the release mirror's
// manifest.json, which advertises the latest published infra binaries.
package manifest

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
)

// Binary describes one published artifact (one OS/arch).
type Binary struct {
	URL    string `json:"url"`
	SHA256 string `json:"sha256"`
}

// Manifest is the top-level document served at <mirror>/manifest.json.
type Manifest struct {
	Version     string            `json:"version"`
	Commit      string            `json:"commit"`
	PublishedAt string            `json:"published_at"`
	Binaries    map[string]Binary `json:"binaries"`
}

// Parse decodes a Manifest from r.
func Parse(r io.Reader) (*Manifest, error) {
	var m Manifest
	dec := json.NewDecoder(r)
	dec.DisallowUnknownFields()
	if err := dec.Decode(&m); err != nil {
		return nil, fmt.Errorf("decode manifest: %w", err)
	}
	if len(m.Binaries) == 0 {
		return nil, fmt.Errorf("decode manifest: no binaries defined")
	}
	return &m, nil
}

// ForArch returns the binary for "<goos>/<goarch>" or an error if missing.
func (m *Manifest) ForArch(goosGoarch string) (Binary, error) {
	b, ok := m.Binaries[goosGoarch]
	if !ok {
		return Binary{}, fmt.Errorf("no binary for %s in manifest", goosGoarch)
	}
	return b, nil
}

// Newer reports whether latest is strictly newer than current.
// Both arguments are expected in `vMAJOR.MINOR.PATCH` form (additional
// suffixes like `-2-gabc-dirty` are tolerated and treated as the base tag's
// pre-release). The literal "dev" and the empty string are treated as older
// than any tagged version.
func Newer(latest, current string) bool {
	if current == "" || current == "dev" {
		return latest != "" && latest != "dev"
	}
	la := parseSemver(latest)
	cu := parseSemver(current)
	for i := 0; i < 3; i++ {
		if la[i] != cu[i] {
			return la[i] > cu[i]
		}
	}
	// Cores are equal. If current has a pre-release/build suffix and latest
	// doesn't, latest is the "real" tag and is newer.
	currentHasSuffix := strings.ContainsAny(current, "-+")
	latestHasSuffix := strings.ContainsAny(latest, "-+")
	if currentHasSuffix && !latestHasSuffix {
		return true
	}
	return false
}

func parseSemver(v string) [3]int {
	v = strings.TrimPrefix(v, "v")
	// Strip any suffix after the third dot-separated number.
	core := v
	if i := strings.IndexAny(v, "-+"); i >= 0 {
		core = v[:i]
	}
	parts := strings.SplitN(core, ".", 3)
	out := [3]int{}
	for i := 0; i < len(parts) && i < 3; i++ {
		n, _ := strconv.Atoi(parts[i])
		out[i] = n
	}
	return out
}

// Fetch GETs a manifest from url. Returns an error on non-200 responses,
// transport errors, or context cancellation.
func Fetch(ctx context.Context, url string) (*Manifest, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, fmt.Errorf("manifest request: %w", err)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("manifest fetch: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("manifest fetch: HTTP %d", resp.StatusCode)
	}
	return Parse(resp.Body)
}
