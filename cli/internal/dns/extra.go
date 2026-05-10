package dns

import (
	"errors"
	"fmt"
	"os"
	"sort"

	"gopkg.in/yaml.v3"
)

// ExtraEntry is one direct (non-Caddy) DNS record.
type ExtraEntry struct {
	Name string `yaml:"name"`
	IP   string `yaml:"ip"`
}

// ReadExtra parses stacks/dns-extra.yaml. A missing file is not an error;
// an empty list is returned.
func ReadExtra(path string) ([]ExtraEntry, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, nil
		}
		return nil, fmt.Errorf("read %s: %w", path, err)
	}
	var entries []ExtraEntry
	if err := yaml.Unmarshal(data, &entries); err != nil {
		return nil, fmt.Errorf("parse %s: %w", path, err)
	}
	return entries, nil
}

// WriteExtra serialises entries (sorted by Name) to stacks/dns-extra.yaml.
func WriteExtra(path string, entries []ExtraEntry) error {
	sorted := make([]ExtraEntry, len(entries))
	copy(sorted, entries)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i].Name < sorted[j].Name })
	out, err := yaml.Marshal(sorted)
	if err != nil {
		return err
	}
	header := []byte("# Direct (non-Caddy) DNS records — managed by `infra dns`.\n")
	return os.WriteFile(path, append(header, out...), 0o644)
}
