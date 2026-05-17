// Package discover walks stacks/ct-*/docker-compose.yml files and builds
// a map of service name → CT(s) that host them.
package discover

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"

	"gopkg.in/yaml.v3"
)

// ServiceLocation points to one place a service runs.
type ServiceLocation struct {
	CT          string // e.g. "ct-media"
	ComposePath string // absolute path to that CT's docker-compose.yml
}

// Host describes how to reach a fleet member over SSH.
type Host struct {
	IP string `json:"ip" yaml:"ip"`
}

// Index maps service names to their locations and host names to their SSH
// coordinates. A single service name may map to multiple CTs (e.g.
// portainer-agent).
type Index struct {
	Hosts    map[string]Host
	Services map[string][]ServiceLocation
}

// SSHTarget returns the form to pass to `ssh` for the given fleet host
// ("root@<ip>" when the inventory has an entry, or the bare name as a
// fallback so existing ~/.ssh/config aliases keep working).
func (i *Index) SSHTarget(host string) string {
	if h, ok := i.Hosts[host]; ok && h.IP != "" {
		return "root@" + h.IP
	}
	return host
}

// All returns the sorted list of distinct service names.
func (i *Index) All() []string {
	names := make([]string, 0, len(i.Services))
	for k := range i.Services {
		names = append(names, k)
	}
	sort.Strings(names)
	return names
}

// Walk finds every stacks/ct-*/docker-compose.yml under repoRoot and
// populates an Index.
func Walk(repoRoot string) (*Index, error) {
	stacksDir := filepath.Join(repoRoot, "stacks")
	entries, err := os.ReadDir(stacksDir)
	if err != nil {
		return nil, fmt.Errorf("read stacks dir %s: %w", stacksDir, err)
	}

	idx := &Index{Services: make(map[string][]ServiceLocation)}

	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		name := e.Name()
		// Only consider ct-* subdirectories.
		if len(name) < 4 || name[:3] != "ct-" {
			continue
		}
		composePath := filepath.Join(stacksDir, name, "docker-compose.yml")
		data, err := os.ReadFile(composePath)
		if err != nil {
			if os.IsNotExist(err) {
				continue
			}
			return nil, fmt.Errorf("read %s: %w", composePath, err)
		}
		var doc struct {
			Services map[string]any `yaml:"services"`
		}
		if err := yaml.Unmarshal(data, &doc); err != nil {
			return nil, fmt.Errorf("parse %s: %w", composePath, err)
		}
		for svc := range doc.Services {
			idx.Services[svc] = append(idx.Services[svc], ServiceLocation{
				CT:          name,
				ComposePath: composePath,
			})
		}
	}

	// Sort each slice for deterministic output.
	for k := range idx.Services {
		sort.Slice(idx.Services[k], func(a, b int) bool {
			return idx.Services[k][a].CT < idx.Services[k][b].CT
		})
	}
	return idx, nil
}
