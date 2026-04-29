// snapshot-services walks the repo's stacks/ct-*/docker-compose.yml tree
// and emits a flat {service: [ct, ...]} JSON map to stdout. The output is
// embedded into the infra binary at build time so consumers can resolve
// service → CT without a local repo checkout.
//
// Usage: snapshot-services <repo-root>
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"sort"

	"github.com/psychonaut0/infra/cli/internal/discover"
)

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: snapshot-services <repo-root>")
		os.Exit(2)
	}
	idx, err := discover.Walk(os.Args[1])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	flat := make(map[string][]string, len(idx.Services))
	for svc, locs := range idx.Services {
		cts := make([]string, len(locs))
		for i, l := range locs {
			cts[i] = l.CT
		}
		sort.Strings(cts)
		flat[svc] = cts
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	if err := enc.Encode(flat); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
