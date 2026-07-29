package dns

import "testing"

func TestParseShadowHosts_ParsesAndSorts(t *testing.T) {
	got := ParseShadowHosts([]string{
		"192.168.3.12 portainer.lan",
		"192.168.3.14 mc-vanilla.lan",
		"192.168.3.12 mgmt.lan",
	})
	if len(got) != 3 {
		t.Fatalf("got %d records, want 3: %+v", len(got), got)
	}
	// Sorted by hostname so the drift report is stable between runs.
	want := []string{"mc-vanilla.lan", "mgmt.lan", "portainer.lan"}
	for i, w := range want {
		if got[i].Hostname != w {
			t.Errorf("record %d hostname = %q, want %q", i, got[i].Hostname, w)
		}
	}
	if got[1].IP != "192.168.3.12" {
		t.Errorf("mgmt.lan IP = %q, want 192.168.3.12", got[1].IP)
	}
}

func TestParseShadowHosts_EmptyInput(t *testing.T) {
	if got := ParseShadowHosts(nil); len(got) != 0 {
		t.Errorf("nil input should give no records, got %+v", got)
	}
	if got := ParseShadowHosts([]string{}); len(got) != 0 {
		t.Errorf("empty input should give no records, got %+v", got)
	}
}

func TestParseShadowHosts_SkipsMalformed(t *testing.T) {
	// A drift report must not invent records from lines it cannot parse.
	got := ParseShadowHosts([]string{
		"192.168.3.12 good.lan",
		"only-one-field",
		"192.168.3.12 too many fields here",
		"",
		"   ",
	})
	if len(got) != 1 {
		t.Fatalf("got %d records, want only the well-formed one: %+v", len(got), got)
	}
	if got[0].Hostname != "good.lan" || got[0].IP != "192.168.3.12" {
		t.Errorf("wrong record kept: %+v", got[0])
	}
}

func TestParseShadowHosts_TolerantOfExtraWhitespace(t *testing.T) {
	got := ParseShadowHosts([]string{"  192.168.3.12\tspaced.lan  "})
	if len(got) != 1 {
		t.Fatalf("got %d records, want 1", len(got))
	}
	if got[0].Hostname != "spaced.lan" || got[0].IP != "192.168.3.12" {
		t.Errorf("whitespace not handled: %+v", got[0])
	}
}

// --- ExtractDNSHosts ---
//
// The fixtures mirror the real pihole.toml shape: a [dns] section, and a
// separate [dhcp] section that also has a `hosts` key meaning something else.

const tomlEmptySingleLine = `[dns]
  upstreams = ["1.1.1.1"]
  hosts = []
  domain = "lan"

[dhcp]
  active = false
  hosts = []
`

const tomlMultiLine = `[dns]
  upstreams = ["1.1.1.1"]
  hosts = [
    "192.168.3.12 portainer.lan",
    "192.168.3.14 mc-vanilla.lan"
  ] ### CHANGED, default = []
  domain = "lan"

[dhcp]
  hosts = [
    "should-never-be-read"
  ]
`

func TestExtractDNSHosts_EmptySingleLine(t *testing.T) {
	// Regression: the previous line-range implementation treated `hosts = []`
	// as an opening bracket and swallowed the rest of the file, reporting HTTP
	// header values as DNS records.
	if got := ExtractDNSHosts([]byte(tomlEmptySingleLine)); len(got) != 0 {
		t.Errorf("empty array must yield no entries, got %+v", got)
	}
}

func TestExtractDNSHosts_MultiLine(t *testing.T) {
	got := ExtractDNSHosts([]byte(tomlMultiLine))
	if len(got) != 2 {
		t.Fatalf("got %d entries, want 2: %+v", len(got), got)
	}
	if got[0] != "192.168.3.12 portainer.lan" || got[1] != "192.168.3.14 mc-vanilla.lan" {
		t.Errorf("wrong entries: %+v", got)
	}
}

func TestExtractDNSHosts_IgnoresDhcpSection(t *testing.T) {
	// pihole.toml has a second `hosts` key under [dhcp] that is unrelated.
	for _, doc := range []string{tomlEmptySingleLine, tomlMultiLine} {
		for _, e := range ExtractDNSHosts([]byte(doc)) {
			if e == "should-never-be-read" {
				t.Fatal("read the [dhcp] hosts array instead of [dns]")
			}
		}
	}
}

func TestExtractDNSHosts_InlineWithEntries(t *testing.T) {
	got := ExtractDNSHosts([]byte("[dns]\n  hosts = [\"10.0.0.1 a.lan\"]\n"))
	if len(got) != 1 || got[0] != "10.0.0.1 a.lan" {
		t.Errorf("inline array not handled: %+v", got)
	}
}

func TestExtractDNSHosts_NoDnsSection(t *testing.T) {
	if got := ExtractDNSHosts([]byte("[dhcp]\n  hosts = [\"x\"]\n")); len(got) != 0 {
		t.Errorf("no [dns] section should yield nothing, got %+v", got)
	}
}

func TestExtractDNSHosts_EndToEndWithParse(t *testing.T) {
	recs := ParseShadowHosts(ExtractDNSHosts([]byte(tomlMultiLine)))
	if len(recs) != 2 {
		t.Fatalf("got %d records, want 2", len(recs))
	}
	if recs[0].Hostname != "mc-vanilla.lan" {
		t.Errorf("expected sorted output, got %+v", recs)
	}
}
