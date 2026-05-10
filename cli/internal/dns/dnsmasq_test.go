package dns

import (
	"strings"
	"testing"
)

func TestRenderDnsmasq_Sorted(t *testing.T) {
	in := []Record{
		{"jellyfin.lan", "192.168.3.12"},
		{"backup.lan", "192.168.3.12"},
		{"mc-vanilla.lan", "192.168.3.14"},
	}
	out := RenderDnsmasq(in)
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	wantOrder := []string{
		"address=/backup.lan/192.168.3.12",
		"address=/jellyfin.lan/192.168.3.12",
		"address=/mc-vanilla.lan/192.168.3.14",
	}
	got := []string{}
	for _, l := range lines {
		if strings.HasPrefix(l, "address=") {
			got = append(got, l)
		}
	}
	if len(got) != len(wantOrder) {
		t.Fatalf("got %d records, want %d", len(got), len(wantOrder))
	}
	for i := range got {
		if got[i] != wantOrder[i] {
			t.Errorf("[%d] got %q want %q", i, got[i], wantOrder[i])
		}
	}
}

func TestRenderDnsmasq_HasHeader(t *testing.T) {
	out := RenderDnsmasq([]Record{{"x.lan", "1.2.3.4"}})
	if !strings.HasPrefix(string(out), "# Managed by `infra dns`") {
		t.Errorf("missing header in:\n%s", out)
	}
}

func TestParseDnsmasq_RoundTrip(t *testing.T) {
	in := []Record{
		{"a.lan", "192.168.3.12"},
		{"b.lan", "192.168.3.14"},
	}
	out := RenderDnsmasq(in)
	got, err := ParseDnsmasq(out)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 {
		t.Fatalf("got %d records, want 2", len(got))
	}
	if got[0].Hostname != "a.lan" || got[1].IP != "192.168.3.14" {
		t.Errorf("round trip mismatch: %+v", got)
	}
}

func TestParseDnsmasq_IgnoresUnrelated(t *testing.T) {
	in := []byte("# comment\nlog-queries\naddress=/x.lan/1.2.3.4\n")
	got, err := ParseDnsmasq(in)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].Hostname != "x.lan" {
		t.Errorf("got %+v", got)
	}
}
