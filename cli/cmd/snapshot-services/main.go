// snapshot-services reads stacks/hosts.yaml and walks stacks/ct-*/docker-compose.yml,
// emitting a combined {hosts, services} JSON map to stdout. The output is
// embedded into the infra binary at build time so it can resolve service
// → CT and CT → SSH target without a local repo checkout.
//
// Usage: snapshot-services <repo-root>
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"

	"github.com/psychonaut0/infra/cli/internal/discover"
	"gopkg.in/yaml.v3"
)

type snapshotFile struct {
	Hosts    map[string]discover.Host `json:"hosts"`
	Services map[string][]string      `json:"services"`
}

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: snapshot-services <repo-root>")
		os.Exit(2)
	}
	root := os.Args[1]

	// 1. Hosts from stacks/hosts.yaml.
	hostsPath := filepath.Join(root, "stacks", "hosts.yaml")
	data, err := os.ReadFile(hostsPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "read %s: %v\n", hostsPath, err)
		os.Exit(1)
	}
	var hosts map[string]discover.Host
	if err := yaml.Unmarshal(data, &hosts); err != nil {
		fmt.Fprintf(os.Stderr, "parse %s: %v\n", hostsPath, err)
		os.Exit(1)
	}

	// 2. Services from compose-file walk.
	idx, err := discover.Walk(root)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	services := make(map[string][]string, len(idx.Services))
	for svc, locs := range idx.Services {
		cts := make([]string, len(locs))
		for i, l := range locs {
			cts[i] = l.CT
		}
		sort.Strings(cts)
		services[svc] = cts
	}

	out := snapshotFile{Hosts: hosts, Services: services}
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	if err := enc.Encode(out); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
