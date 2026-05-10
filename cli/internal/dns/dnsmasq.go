package dns

import (
	"bufio"
	"bytes"
	"fmt"
	"sort"
	"strings"
	"time"
)

// Record is one dnsmasq host record (address=/hostname/ip).
type Record struct {
	Hostname string
	IP       string
}

const dnsmasqHeader = `# Managed by ` + "`infra dns`" + `. Do not edit by hand.
# Generated %s

`

// RenderDnsmasq emits the full content of /etc/dnsmasq.d/02-infra-dns.conf.
// Records are sorted alphabetically by hostname for stable diffs.
func RenderDnsmasq(records []Record) []byte {
	sorted := make([]Record, len(records))
	copy(sorted, records)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i].Hostname < sorted[j].Hostname })
	var b bytes.Buffer
	fmt.Fprintf(&b, dnsmasqHeader, time.Now().UTC().Format(time.RFC3339))
	for _, r := range sorted {
		fmt.Fprintf(&b, "address=/%s/%s\n", r.Hostname, r.IP)
	}
	return b.Bytes()
}

// ParseDnsmasq extracts host records from the managed dnsmasq config. Lines
// that don't match `address=/host/ip` are ignored.
func ParseDnsmasq(content []byte) ([]Record, error) {
	var out []Record
	scanner := bufio.NewScanner(bytes.NewReader(content))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if !strings.HasPrefix(line, "address=/") {
			continue
		}
		parts := strings.Split(strings.TrimPrefix(line, "address=/"), "/")
		if len(parts) != 2 {
			continue
		}
		out = append(out, Record{Hostname: parts[0], IP: parts[1]})
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("scan dnsmasq: %w", err)
	}
	return out, nil
}
