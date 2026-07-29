package dns

import (
	"bufio"
	"bytes"
	"sort"
	"strings"
)

// ShadowRecord is a DNS record found in pihole.toml's `[dns] hosts` array.
//
// These are "shadow" records because `infra dns` does not manage that array —
// it manages /etc/dnsmasq.d/02-infra-dns.conf. Pi-hole's own web UI writes
// pihole.toml, so a record added there is invisible to this tool: it resolves
// on the network while `infra dns ls`/`sync` report nothing, which is exactly
// how 17 duplicated records and one unmanaged hostname (mgmt.lan) accumulated
// unnoticed before 2026-07-29.
//
// Anything found here is drift by definition and needs a human decision:
// adopt it into the repo, or delete it from pihole.toml.
type ShadowRecord struct {
	Hostname string `json:"hostname"`
	IP       string `json:"ip"`
}

// ExtractDNSHosts returns the raw `"<ip> <hostname>"` entries of the `hosts`
// array in pihole.toml's `[dns]` section.
//
// Section scoping is essential, not cosmetic: pihole.toml has a second `hosts`
// key under `[dhcp]` that means something entirely different. An earlier
// implementation used a line-range match with no section awareness and, once
// the DNS array was written in its empty single-line form (`hosts = []`),
// treated that as an opening bracket and swallowed everything up to the next
// `]` — reporting HTTP header values as DNS records.
//
// Both array forms are handled: `hosts = []` on one line, and a multi-line
// array terminated by a lone `]`.
func ExtractDNSHosts(toml []byte) []string {
	var (
		section string
		out     []string
	)
	sc := bufio.NewScanner(bytes.NewReader(toml))
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		line := sc.Text()
		trimmed := strings.TrimSpace(line)

		if strings.HasPrefix(trimmed, "[") && strings.HasSuffix(trimmed, "]") &&
			!strings.Contains(trimmed, "=") {
			section = strings.Trim(trimmed, "[]")
			continue
		}
		if section != "dns" {
			continue
		}
		key, value, found := strings.Cut(trimmed, "=")
		if !found || strings.TrimSpace(key) != "hosts" {
			continue
		}
		value = strings.TrimSpace(value)
		if !strings.HasPrefix(value, "[") {
			continue
		}
		// Single-line form: everything is on this line.
		if strings.Contains(value[1:], "]") {
			return quotedStrings(value)
		}
		// Multi-line form: collect until a line that closes the array.
		out = append(out, quotedStrings(value)...)
		for sc.Scan() {
			l := strings.TrimSpace(sc.Text())
			out = append(out, quotedStrings(l)...)
			if strings.HasPrefix(l, "]") {
				break
			}
		}
		return out
	}
	return out
}

// quotedStrings returns the contents of every double-quoted run in s.
func quotedStrings(s string) []string {
	var out []string
	for {
		i := strings.IndexByte(s, '"')
		if i < 0 {
			return out
		}
		rest := s[i+1:]
		j := strings.IndexByte(rest, '"')
		if j < 0 {
			return out
		}
		if v := rest[:j]; v != "" {
			out = append(out, v)
		}
		s = rest[j+1:]
	}
}

// ParseShadowHosts turns raw `"<ip> <hostname>"` entries into records, sorted
// by hostname.
//
// Malformed entries — anything that is not exactly two fields — are skipped
// rather than guessed at: this feeds a drift report, and inventing a record
// from a line we do not understand would be worse than omitting it. Note it
// deliberately does NOT validate the IP: a record with a malformed address
// still exists and still needs reporting. Callers that act on these records
// (rather than merely reporting them) must validate themselves.
func ParseShadowHosts(lines []string) []ShadowRecord {
	var out []ShadowRecord
	for _, l := range lines {
		fields := strings.Fields(l)
		if len(fields) != 2 {
			continue
		}
		out = append(out, ShadowRecord{IP: fields[0], Hostname: fields[1]})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Hostname < out[j].Hostname })
	return out
}

// ShadowRemediation is the operator-facing instruction printed when shadow
// records are found. `infra dns` deliberately does not edit pihole.toml itself:
// Pi-hole's web UI owns that file, so a concurrent write could clobber a real
// change. Removal stays a deliberate manual act.
const ShadowRemediation = `These records live in pihole.toml's [dns] hosts array, which ` + "`infra dns`" + `
does not manage — they resolve on the network but are invisible to this tool.

To resolve, for each record either:
  * adopt it   — ` + "`infra dns add <name> <upstream>`" + ` (or --no-caddy --ip <ip>)
  * or drop it — remove the line from the [dns] hosts array in
                 /etc/pihole/pihole.toml on ct-dns, then restart the container.

Once that array is empty, the managed dnsmasq file is the single source of truth.`
