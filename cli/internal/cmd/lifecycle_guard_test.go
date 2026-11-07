package cmd

import (
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"testing"

	"github.com/psychonaut0/infra/cli/internal/discover"
	"github.com/spf13/cobra"
)

// guardFixture is a synthetic infra checkout. ct-animus carries its compose
// service names (migrate and api behind profiles, as in the real stack) plus
// future-worker, a service no committed snapshot has seen. ct-sample is an
// unguarded neighbour sharing one service name with it. Addresses come from
// the RFC 5737 documentation range.
var guardFixture = map[string]string{
	"stacks/hosts.yaml": "ct-animus:\n  ip: 192.0.2.20\nct-sample:\n  ip: 192.0.2.30\n",
	"stacks/ct-animus/docker-compose.yml": `services:
  app-postgres: {image: synthetic/db}
  auth-postgres: {image: synthetic/db}
  auth-server: {image: synthetic/auth}
  auth-worker: {image: synthetic/auth}
  migrate: {image: synthetic/api, profiles: [operator]}
  api: {image: synthetic/api, profiles: [commissioning]}
  future-worker: {image: synthetic/worker}
  shared-agent: {image: synthetic/agent}
`,
	"stacks/ct-sample/docker-compose.yml": `services:
  whoami: {image: synthetic/whoami}
  shared-agent: {image: synthetic/agent}
`,
}

// writeGuardFixture writes guardFixture under root, with a .git marker so the
// directory is also found by walking up from a working directory inside it.
func writeGuardFixture(t *testing.T, root string) string {
	t.Helper()
	if err := os.MkdirAll(filepath.Join(root, ".git"), 0o755); err != nil {
		t.Fatal(err)
	}
	for rel, body := range guardFixture {
		path := filepath.Join(root, rel)
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

// servicesOn lists, sorted, every service the fixture's compose files place on ct.
func servicesOn(t *testing.T, idx *discover.Index, ct string) []string {
	t.Helper()
	var out []string
	for svc, locs := range idx.Services {
		for _, l := range locs {
			if l.CT == ct {
				out = append(out, svc)
			}
		}
	}
	sort.Strings(out)
	if len(out) == 0 {
		t.Fatalf("fixture has no services on %s", ct)
	}
	return out
}

func assertGuardRefusal(t *testing.T, err error, target string) {
	t.Helper()
	if !errors.Is(err, ErrGuardedLifecycle) {
		t.Fatalf("%q: want ErrGuardedLifecycle, got %v", target, err)
	}
	for _, want := range []string{"ct-animus", "animus-stack.service", "stacks/ct-animus/ops/README.md"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("%q: refusal %q does not mention %q", target, err, want)
		}
	}
}

func TestCheckLifecycleGuard(t *testing.T) {
	idx, err := discover.Walk(writeGuardFixture(t, t.TempDir()))
	if err != nil {
		t.Fatal(err)
	}
	load := func() (*discover.Index, error) { return idx, nil }
	noLoad := func() (*discover.Index, error) {
		t.Fatal("index loaded for a target that names its CT")
		return nil, nil
	}

	// Forms that name the CT are refused without consulting the index.
	for _, target := range []string{"ct-animus", "ct-animus:api", "ct-animus:migrate", "ct-animus:not-in-compose"} {
		for _, verb := range []string{"deploy", "restart"} {
			assertGuardRefusal(t, checkLifecycleGuard(verb, []string{target}, noLoad), target)
		}
	}

	// Every service the index places on ct-animus, bare or with an empty CT
	// prefix (which Resolve treats as unqualified), plus a name it shares.
	for _, svc := range servicesOn(t, idx, "ct-animus") {
		for _, target := range []string{svc, ":" + svc} {
			assertGuardRefusal(t, checkLifecycleGuard("deploy", []string{target}, load), target)
			assertGuardRefusal(t, checkLifecycleGuard("restart", []string{target}, load), target)
		}
	}

	// Targets that cannot reach ct-animus pass through to the command.
	for _, target := range []string{"whoami", "ct-sample", "ct-sample:whoami", "ct-sample:shared-agent", "ct-sample:api", "unknown"} {
		if err := checkLifecycleGuard("deploy", []string{target}, load); err != nil {
			t.Errorf("%q: want pass-through, got %v", target, err)
		}
	}

	// A failed index load refuses rather than guessing.
	loadErr := errors.New("synthetic load failure")
	failing := func() (*discover.Index, error) { return nil, loadErr }
	if err := checkLifecycleGuard("deploy", []string{"api"}, failing); !errors.Is(err, loadErr) {
		t.Errorf("load failure: want %v, got %v", loadErr, err)
	}
}

func TestLifecycleGuardWiring(t *testing.T) {
	root := &cobra.Command{Use: "infra"}
	addCommands(root)
	guarded := map[string]bool{"deploy": true, "restart": true}
	for _, name := range []string{"deploy", "restart", "ls", "status", "logs"} {
		c, _, err := root.Find([]string{name})
		if err != nil || c.Name() != name {
			t.Fatalf("command %s not registered: %v", name, err)
		}
		if got := c.PreRunE != nil; got != guarded[name] {
			t.Errorf("%s: guarded = %v, want %v", name, got, guarded[name])
		}
	}
}

func TestWithLifecycleGuardKeepsExistingPreRunE(t *testing.T) {
	t.Setenv("INFRA_REPO", writeGuardFixture(t, t.TempDir()))
	ran := 0
	c := withLifecycleGuard(&cobra.Command{
		Use:     "deploy",
		PreRunE: func(*cobra.Command, []string) error { ran++; return nil },
	})
	if err := c.PreRunE(c, []string{"whoami"}); err != nil || ran != 1 {
		t.Fatalf("allowed target: err=%v, earlier PreRunE ran %d times", err, ran)
	}
	assertGuardRefusal(t, c.PreRunE(c, []string{"api"}), "api")
	if ran != 1 {
		t.Errorf("earlier PreRunE ran after a refusal")
	}
}

// fakeSSH records every ssh invocation, drains stdin (deploy streams a tar to
// it) and answers `docker ps` with one synthetic container.
const fakeSSH = `#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_SSH_LOG"
cat > /dev/null
case "$*" in
*"docker ps"*) printf 'synthetic-1|running|Up 1 minute\n' ;;
esac
exit 0
`

// buildCLIWithSnapshot builds cmd/infra with an embedded fleet snapshot that is
// regenerated from fixture by the release workflow's own "Snapshot fleet"
// command (`go run ./cmd/snapshot-services <root>`). An -overlay swaps it in,
// so the committed fleet.json is never touched.
func buildCLIWithSnapshot(t *testing.T, fixture, work string) string {
	t.Helper()
	goBin, err := exec.LookPath("go")
	if err != nil {
		goBin = filepath.Join(os.Getenv("GOROOT"), "bin", "go")
	}
	moduleRoot, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatal(err)
	}

	gen := exec.Command(goBin, "run", "./cmd/snapshot-services", fixture)
	gen.Dir = moduleRoot
	snapshot, err := gen.Output()
	if err != nil {
		t.Fatalf("snapshot-services: %v", err)
	}
	snapshotPath := filepath.Join(work, "fleet.json")
	if err := os.WriteFile(snapshotPath, snapshot, 0o644); err != nil {
		t.Fatal(err)
	}
	overlay, err := json.Marshal(map[string]map[string]string{"Replace": {
		filepath.Join(moduleRoot, "internal", "discover", "fleet.json"): snapshotPath,
	}})
	if err != nil {
		t.Fatal(err)
	}
	overlayPath := filepath.Join(work, "overlay.json")
	if err := os.WriteFile(overlayPath, overlay, 0o644); err != nil {
		t.Fatal(err)
	}

	bin := filepath.Join(work, "infra")
	build := exec.Command(goBin, "build", "-trimpath", "-overlay", overlayPath, "-o", bin, "./cmd/infra")
	build.Dir = moduleRoot
	if out, err := build.CombinedOutput(); err != nil {
		t.Fatalf("go build: %v\n%s", err, out)
	}
	return bin
}

type cliResult struct {
	err    error
	stdout string
	stderr string
	ssh    string
}

// TestLifecycleGuardCLI runs the built CLI in both discovery modes: checkout
// (INFRA_REPO, and a working directory inside the checkout) and snapshot (no
// checkout reachable, so the regenerated embedded snapshot is the index).
func TestLifecycleGuardCLI(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("fake ssh is a POSIX shell script")
	}
	if testing.Short() {
		t.Skip("builds the CLI binary")
	}
	work := t.TempDir()
	fixture := writeGuardFixture(t, filepath.Join(work, "repo"))
	bin := buildCLIWithSnapshot(t, fixture, work)

	sshDir := filepath.Join(work, "fake-bin")
	if err := os.MkdirAll(sshDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(sshDir, "ssh"), []byte(fakeSSH), 0o755); err != nil {
		t.Fatal(err)
	}
	outside := filepath.Join(work, "outside")
	if err := os.MkdirAll(outside, 0o755); err != nil {
		t.Fatal(err)
	}
	for dir := outside; ; dir = filepath.Dir(dir) {
		if _, err := os.Stat(filepath.Join(dir, ".git")); err == nil {
			t.Skipf("snapshot mode needs a directory outside any checkout; %s has .git", dir)
		}
		if filepath.Dir(dir) == dir {
			break
		}
	}

	idx, err := discover.Walk(fixture)
	if err != nil {
		t.Fatal(err)
	}
	animusServices := servicesOn(t, idx, "ct-animus")
	sampleServices := servicesOn(t, idx, "ct-sample")

	baseEnv := []string{
		"PATH=" + sshDir + string(os.PathListSeparator) + os.Getenv("PATH"),
		"HOME=" + filepath.Join(work, "home"),
		"XDG_CACHE_HOME=" + filepath.Join(work, "cache"),
		"INFRA_NO_UPDATE_CHECK=1",
	}
	modes := []struct {
		name string
		dir  string
		env  []string
	}{
		{"checkout/INFRA_REPO", outside, []string{"INFRA_REPO=" + fixture}},
		{"checkout/cwd", filepath.Join(fixture, "stacks", "ct-animus"), nil},
		{"snapshot", outside, nil},
	}

	calls := 0
	for _, mode := range modes {
		t.Run(mode.name, func(t *testing.T) {
			run := func(t *testing.T, args ...string) cliResult {
				t.Helper()
				calls++
				log := filepath.Join(work, "ssh-"+strconv.Itoa(calls)+".log")
				cmd := exec.Command(bin, args...)
				cmd.Dir = mode.dir
				cmd.Env = append(append([]string{"FAKE_SSH_LOG=" + log}, baseEnv...), mode.env...)
				var stdout, stderr strings.Builder
				cmd.Stdout, cmd.Stderr = &stdout, &stderr
				err := cmd.Run()
				sshLog, _ := os.ReadFile(log)
				return cliResult{err: err, stdout: stdout.String(), stderr: stderr.String(), ssh: string(sshLog)}
			}

			refused := [][]string{
				{"deploy", "--yes", "ct-animus"},
				{"deploy", "ct-animus"}, // refusal comes before the confirmation prompt
				{"deploy", "--yes", "ct-animus:api"},
				{"deploy", "--yes", "ct-animus:migrate"},
				{"deploy", "--yes", "ct-animus:shared-agent"},
				{"deploy", "--yes", ":api"},
				{"restart", "--yes", "ct-animus:auth-server"},
				{"restart", "--yes", ":auth-worker"},
				{"restart", "--yes", "ct-animus"},
			}
			for _, svc := range animusServices {
				refused = append(refused, []string{"deploy", "--yes", svc}, []string{"restart", "--yes", svc})
			}
			for _, args := range refused {
				t.Run(strings.Join(args, " "), func(t *testing.T) {
					r := run(t, args...)
					if r.err == nil {
						t.Fatalf("exit 0, want refusal; stderr: %s", r.stderr)
					}
					for _, want := range []string{"guarded lifecycle", "animus-stack.service", "stacks/ct-animus/ops/README.md"} {
						if !strings.Contains(r.stderr, want) {
							t.Errorf("stderr lacks %q: %s", want, r.stderr)
						}
					}
					if strings.Contains(r.stderr, "192.0.2.") {
						t.Errorf("refusal prints an SSH address: %s", r.stderr)
					}
					if r.ssh != "" {
						t.Errorf("refused command reached ssh: %s", r.ssh)
					}
				})
			}

			allowed := []struct {
				args []string
				want string
			}{
				{[]string{"deploy", "--yes", "whoami"}, "root@192.0.2.30 docker compose -f /opt/stacks/ct-sample/docker-compose.yml pull whoami && docker compose -f /opt/stacks/ct-sample/docker-compose.yml up -d --force-recreate whoami"},
				{[]string{"deploy", "--yes", "ct-sample:shared-agent"}, "up -d --force-recreate shared-agent"},
				{[]string{"deploy", "--yes", "ct-sample"}, "root@192.0.2.30 docker compose -f /opt/stacks/ct-sample/docker-compose.yml pull  && "},
				{[]string{"restart", "--yes", "whoami"}, "root@192.0.2.30 docker compose -f /opt/stacks/ct-sample/docker-compose.yml restart whoami"},
				{[]string{"logs", "--no-follow", "api"}, "root@192.0.2.20 docker logs --tail 100 api"},
				{[]string{"logs", "--no-follow", "migrate"}, "root@192.0.2.20 docker logs --tail 100 migrate"},
				{[]string{"logs", "--no-follow", "ct-animus:future-worker"}, "root@192.0.2.20 docker logs --tail 100 future-worker"},
				{[]string{"status", "--ct", "ct-animus", "--json"}, "root@192.0.2.20 docker ps -a"},
			}
			for _, a := range allowed {
				t.Run(strings.Join(a.args, " "), func(t *testing.T) {
					r := run(t, a.args...)
					if r.err != nil {
						t.Fatalf("%v; stderr: %s", r.err, r.stderr)
					}
					if !strings.Contains(r.ssh, a.want) {
						t.Errorf("ssh calls %q lack %q", r.ssh, a.want)
					}
				})
			}

			t.Run("ls lists ct-animus", func(t *testing.T) {
				r := run(t, "ls", "--json")
				if r.err != nil {
					t.Fatalf("%v; stderr: %s", r.err, r.stderr)
				}
				var byCT map[string][]string
				if err := json.Unmarshal([]byte(r.stdout), &byCT); err != nil {
					t.Fatalf("parse ls: %v\n%s", err, r.stdout)
				}
				want := map[string][]string{"ct-animus": animusServices, "ct-sample": sampleServices}
				if !reflect.DeepEqual(byCT, want) {
					t.Errorf("ls = %v, want %v", byCT, want)
				}
			})

			t.Run("status lists ct-animus", func(t *testing.T) {
				r := run(t, "status", "--json")
				if r.err != nil {
					t.Fatalf("%v; stderr: %s", r.err, r.stderr)
				}
				var rows []statusRow
				if err := json.Unmarshal([]byte(r.stdout), &rows); err != nil {
					t.Fatalf("parse status: %v\n%s", err, r.stdout)
				}
				cts := map[string]bool{}
				for _, row := range rows {
					cts[row.CT] = true
				}
				if !cts["ct-animus"] || !cts["ct-sample"] {
					t.Errorf("status CTs = %v, want ct-animus and ct-sample", cts)
				}
			})
		})
	}
}
