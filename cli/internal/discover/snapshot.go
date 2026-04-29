package discover

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"sort"
)

//go:embed services.json
var snapshotJSON []byte

// LoadSnapshot returns an Index built from the binary's embedded fleet
// snapshot. The snapshot is regenerated at build time from the repo's
// stacks/ tree. Consumers running outside a repo checkout (e.g. CTs that
// installed via the LAN mirror) fall back to this.
func LoadSnapshot() (*Index, error) {
	if len(snapshotJSON) == 0 {
		return &Index{Services: map[string][]ServiceLocation{}},
			fmt.Errorf("no embedded fleet snapshot in this binary; rebuild with `make snapshot`")
	}
	var raw map[string][]string
	if err := json.Unmarshal(snapshotJSON, &raw); err != nil {
		return nil, fmt.Errorf("parse embedded snapshot: %w", err)
	}
	idx := &Index{Services: make(map[string][]ServiceLocation, len(raw))}
	for svc, cts := range raw {
		sort.Strings(cts)
		for _, ct := range cts {
			idx.Services[svc] = append(idx.Services[svc], ServiceLocation{CT: ct})
		}
	}
	return idx, nil
}

// Load tries the repo-based walk first; if no repo is found, falls back to
// the embedded snapshot. The repoLocate argument is typically `repo.Locate`.
func Load(repoLocate func() (string, error)) (*Index, error) {
	if root, err := repoLocate(); err == nil {
		return Walk(root)
	}
	return LoadSnapshot()
}
