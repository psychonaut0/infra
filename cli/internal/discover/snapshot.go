package discover

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"sort"
)

//go:embed fleet.json
var snapshotJSON []byte

// snapshotFile is the JSON shape produced by cmd/snapshot-fleet and embedded
// into the binary.
type snapshotFile struct {
	Hosts    map[string]Host     `json:"hosts"`
	Services map[string][]string `json:"services"`
}

// LoadSnapshot returns an Index built from the binary's embedded fleet
// snapshot. The snapshot is regenerated at build time from stacks/hosts.yaml
// and the per-CT docker-compose.yml files. Consumers running outside a repo
// checkout (e.g. CTs that installed via the LAN mirror) fall back to this.
func LoadSnapshot() (*Index, error) {
	if len(snapshotJSON) == 0 {
		return nil, fmt.Errorf("no embedded fleet snapshot in this binary; rebuild with `make snapshot`")
	}
	var raw snapshotFile
	if err := json.Unmarshal(snapshotJSON, &raw); err != nil {
		return nil, fmt.Errorf("parse embedded snapshot: %w", err)
	}
	idx := &Index{
		Hosts:    raw.Hosts,
		Services: make(map[string][]ServiceLocation, len(raw.Services)),
	}
	for svc, cts := range raw.Services {
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
		idx, err := Walk(root)
		if err != nil {
			return nil, err
		}
		// Walk does not populate Hosts; merge them in from the snapshot so
		// consumers always have an inventory to resolve SSH targets.
		if snap, snapErr := LoadSnapshot(); snapErr == nil {
			idx.Hosts = snap.Hosts
		}
		return idx, nil
	}
	return LoadSnapshot()
}
