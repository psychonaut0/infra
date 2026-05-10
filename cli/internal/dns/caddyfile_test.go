package dns

import (
	"os"
	"testing"
)

func TestParseCaddyfile_Fixture(t *testing.T) {
	data, err := os.ReadFile("testdata/Caddyfile.fixture")
	if err != nil {
		t.Fatal(err)
	}
	blocks, err := ParseCaddyfile(data)
	if err != nil {
		t.Fatalf("ParseCaddyfile: %v", err)
	}
	byHost := map[string]Block{}
	for _, b := range blocks {
		// Merge HTTP/HTTPS pairs by hostname for the test.
		existing, ok := byHost[b.Hostname]
		if ok {
			existing.HasHTTP = existing.HasHTTP || b.HasHTTP
			existing.HasHTTPS = existing.HasHTTPS || b.HasHTTPS
			byHost[b.Hostname] = existing
		} else {
			byHost[b.Hostname] = b
		}
	}
	cases := []struct {
		host     string
		hasHTTP  bool
		hasHTTPS bool
	}{
		{"portainer.lan", true, false},
		{"jellyfin.lan", true, false},
		{"nvr.lan", true, true},
		{"proxmox.lan", false, true},
		{"infra-bin.lan", true, false}, // present even though file_server (raw)
	}
	for _, c := range cases {
		got, ok := byHost[c.host]
		if !ok {
			t.Errorf("%s missing from parsed blocks", c.host)
			continue
		}
		if got.HasHTTP != c.hasHTTP || got.HasHTTPS != c.hasHTTPS {
			t.Errorf("%s: got http=%v https=%v, want http=%v https=%v",
				c.host, got.HasHTTP, got.HasHTTPS, c.hasHTTP, c.hasHTTPS)
		}
	}
}

func TestParseCaddyfile_DetectsManaged(t *testing.T) {
	in := []byte("http://foo.lan {\n\treverse_proxy 192.168.3.99:1234\n}\n")
	blocks, err := ParseCaddyfile(in)
	if err != nil {
		t.Fatal(err)
	}
	if len(blocks) != 1 {
		t.Fatalf("got %d blocks, want 1", len(blocks))
	}
	b := blocks[0]
	if b.Hostname != "foo.lan" || !b.Managed || b.Upstream != "192.168.3.99:1234" {
		t.Errorf("block = %+v", b)
	}
}

func TestParseCaddyfile_DetectsUnmanaged(t *testing.T) {
	in := []byte("http://infra-bin.lan {\n\troot * /srv/infra-bin\n\tfile_server\n}\n")
	blocks, err := ParseCaddyfile(in)
	if err != nil {
		t.Fatal(err)
	}
	if len(blocks) != 1 || blocks[0].Managed {
		t.Errorf("expected one unmanaged block, got %+v", blocks)
	}
}
