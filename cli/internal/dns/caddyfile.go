// Package dns implements infra dns: parsing/manipulating the Caddyfile,
// rendering the managed dnsmasq config, and reconciling the two against
// stacks/dns-extra.yaml.
package dns

import (
	"bufio"
	"bytes"
	"fmt"
	"regexp"
	"strings"
)

// Block describes one top-level Caddyfile site block, classified by what
// `infra dns` can do with it.
type Block struct {
	Hostname string // e.g. "jellyfin.lan"
	HasHTTP  bool   // opening line was `http://hostname {`
	HasHTTPS bool   // opening line was bare `hostname {` (with TLS)
	Upstream string // best-effort upstream from a single-line `reverse_proxy <X>`; empty if none/raw
	Managed  bool   // true if the body is a simple reverse_proxy + optional transport stanza only
}

var openRE = regexp.MustCompile(`^(https?://)?([a-z0-9][a-z0-9.-]*\.lan)\s*\{\s*$`)

// ParseCaddyfile returns one Block per top-level site block matched by the
// `[http(s)://]hostname.lan {` opening pattern. Anything else (comments,
// blank lines, blocks for non-`.lan` hostnames) is ignored for inventory
// purposes but is preserved verbatim by AppendBlock/RemoveBlocks since
// those operate on raw bytes.
func ParseCaddyfile(content []byte) ([]Block, error) {
	var blocks []Block
	scanner := bufio.NewScanner(bytes.NewReader(content))
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	depth := 0
	var current *Block
	var bodyLines []string
	for scanner.Scan() {
		line := scanner.Text()
		trimmed := strings.TrimSpace(line)
		if depth == 0 {
			m := openRE.FindStringSubmatch(trimmed)
			if m != nil {
				current = &Block{Hostname: m[2]}
				switch m[1] {
				case "http://":
					current.HasHTTP = true
				case "https://":
					current.HasHTTPS = true
				default:
					current.HasHTTPS = true
				}
				bodyLines = bodyLines[:0]
				depth = 1
				continue
			}
		}
		if depth > 0 {
			depth += strings.Count(line, "{")
			depth -= strings.Count(line, "}")
			if depth == 0 {
				if current != nil {
					classify(current, bodyLines)
					blocks = append(blocks, *current)
					current = nil
				}
			} else if current != nil {
				bodyLines = append(bodyLines, trimmed)
			}
		}
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("scan caddyfile: %w", err)
	}
	return blocks, nil
}

// AppendBlock appends one new top-level site block to content. listenerScheme
// is "http" or "https". upstream is a URL (http:// or https://) or bare
// host:port — when https://, the block includes a tls_insecure_skip_verify
// transport stanza. The returned bytes always end with a trailing newline.
func AppendBlock(content []byte, hostname, listenerScheme, upstream string) []byte {
	upstreamHTTPS := strings.HasPrefix(upstream, "https://")
	var b strings.Builder
	b.Write(content)
	if len(content) > 0 && !bytes.HasSuffix(content, []byte("\n")) {
		b.WriteByte('\n')
	}
	b.WriteByte('\n')
	switch listenerScheme {
	case "https":
		b.WriteString(hostname)
		b.WriteString(" {\n\ttls internal\n")
	default:
		b.WriteString("http://")
		b.WriteString(hostname)
		b.WriteString(" {\n")
	}
	b.WriteString("\treverse_proxy ")
	b.WriteString(upstream)
	if upstreamHTTPS {
		b.WriteString(" {\n\t\ttransport http {\n\t\t\ttls_insecure_skip_verify\n\t\t}\n\t}")
	}
	b.WriteString("\n}\n")
	return []byte(b.String())
}

func classify(b *Block, body []string) {
	// Managed shape: tls internal? + reverse_proxy <X>; optional transport
	// stanza. Anything else (root/file_server/multiple sites) is unmanaged.
	rp := ""
	hasOther := false
	for _, l := range body {
		switch {
		case l == "" || l == "tls internal":
			// allowed
		case strings.HasPrefix(l, "reverse_proxy "):
			parts := strings.Fields(l)
			if len(parts) >= 2 {
				rp = parts[1]
			}
			// trailing `{` for transport block is consumed by depth tracking
		case l == "transport http {", l == "tls_insecure_skip_verify",
			l == "}", l == "encode gzip":
			// allowed continuation lines for the wrapped reverse_proxy
		default:
			hasOther = true
		}
	}
	b.Upstream = rp
	b.Managed = !hasOther && rp != ""
}
