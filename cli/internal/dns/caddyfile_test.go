package dns

import (
	"bytes"
	"os"
	"strings"
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

func TestAppendBlock_HTTP_PlainUpstream(t *testing.T) {
	in := []byte("http://existing.lan {\n\treverse_proxy 192.168.3.99:1234\n}\n")
	out := AppendBlock(in, "newsvc.lan", "http", "192.168.3.50:8080")
	want := "http://existing.lan {\n\treverse_proxy 192.168.3.99:1234\n}\n\nhttp://newsvc.lan {\n\treverse_proxy 192.168.3.50:8080\n}\n"
	if string(out) != want {
		t.Errorf("got:\n%s\nwant:\n%s", out, want)
	}
}

func TestAppendBlock_HTTPS_TLSUpstream(t *testing.T) {
	in := []byte("")
	out := AppendBlock(in, "https-svc.lan", "https", "https://192.168.3.7:8971")
	want := "\nhttps-svc.lan {\n\ttls internal\n\treverse_proxy https://192.168.3.7:8971 {\n\t\ttransport http {\n\t\t\ttls_insecure_skip_verify\n\t\t}\n\t}\n}\n"
	if string(out) != want {
		t.Errorf("got:\n%q\nwant:\n%q", out, want)
	}
}

func TestAppendBlock_HTTP_TLSUpstream(t *testing.T) {
	in := []byte("")
	out := AppendBlock(in, "frigate.lan", "http", "https://192.168.3.7:8971")
	want := "\nhttp://frigate.lan {\n\treverse_proxy https://192.168.3.7:8971 {\n\t\ttransport http {\n\t\t\ttls_insecure_skip_verify\n\t\t}\n\t}\n}\n"
	if string(out) != want {
		t.Errorf("got:\n%q\nwant:\n%q", out, want)
	}
}

func TestRemoveBlocks_Single(t *testing.T) {
	in := []byte("http://a.lan {\n\treverse_proxy 1.1.1.1:1\n}\n\nhttp://b.lan {\n\treverse_proxy 2.2.2.2:2\n}\n")
	out, n := RemoveBlocks(in, "a.lan")
	if n != 1 {
		t.Errorf("removed %d, want 1", n)
	}
	if strings.Contains(string(out), "a.lan") {
		t.Errorf("output still contains a.lan: %s", out)
	}
	if !strings.Contains(string(out), "b.lan") {
		t.Errorf("output dropped b.lan: %s", out)
	}
}

func TestRemoveBlocks_HTTPandHTTPSPair(t *testing.T) {
	in := []byte("nvr.lan {\n\ttls internal\n\treverse_proxy https://x:1\n}\n\nhttp://nvr.lan {\n\treverse_proxy https://x:1\n}\n")
	out, n := RemoveBlocks(in, "nvr.lan")
	if n != 2 {
		t.Errorf("removed %d, want 2", n)
	}
	if strings.Contains(string(out), "nvr.lan") {
		t.Errorf("output still contains nvr.lan")
	}
}

func TestRemoveBlocks_NoMatch(t *testing.T) {
	in := []byte("http://a.lan {\n\treverse_proxy 1.1.1.1:1\n}\n")
	out, n := RemoveBlocks(in, "missing.lan")
	if n != 0 {
		t.Errorf("removed %d, want 0", n)
	}
	if !bytes.Equal(out, in) {
		t.Errorf("file changed when no match: %s", out)
	}
}

func TestRemoveBlocks_PreservesUnrelated(t *testing.T) {
	data, err := os.ReadFile("testdata/Caddyfile.fixture")
	if err != nil {
		t.Fatal(err)
	}
	out, n := RemoveBlocks(data, "infra-bin.lan")
	if n != 1 {
		t.Errorf("removed %d, want 1", n)
	}
	// All other hostnames must still be present.
	for _, host := range []string{"jellyfin.lan", "proxmox.lan", "nvr.lan"} {
		if !strings.Contains(string(out), host) {
			t.Errorf("removed unrelated host %s", host)
		}
	}
}
