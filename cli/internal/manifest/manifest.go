// Package manifest defines the schema and a parser for the release mirror's
// manifest.json, which advertises the latest published infra binaries.
package manifest

import (
	"encoding/json"
	"fmt"
	"io"
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
