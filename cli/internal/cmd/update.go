package cmd

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/psychonaut0/infra/cli/internal/manifest"
	"github.com/psychonaut0/infra/cli/internal/repo"
	"github.com/psychonaut0/infra/cli/internal/ui"
	"github.com/spf13/cobra"
)

const defaultMirrorURL = "http://infra-bin.lan/manifest.json"

func newUpdateCmd() *cobra.Command {
	var check bool
	var yes bool
	var ref string
	var fromSource bool
	var mirrorURL string

	c := &cobra.Command{
		Use:   "update",
		Short: "Update the infra binary",
		Long:  "Updates the infra binary from the LAN release mirror by default. Use --from-source to build from a local repo checkout instead.",
		RunE: func(cmd *cobra.Command, _ []string) error {
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
			defer cancel()

			if fromSource {
				return runFromSource(ctx, cmd, runFromSourceOpts{
					Check: check,
					Yes:   yes,
					Ref:   ref,
				})
			}

			installTarget, err := installPath()
			if err != nil {
				return err
			}
			currentVer := cmd.Root().Annotations["version"]
			url := mirrorURL
			if !cmd.Flags().Changed("mirror") {
				if env := os.Getenv("INFRA_MIRROR_URL"); env != "" {
					url = env
				}
			}
			return runFromMirror(ctx, runFromMirrorOpts{
				MirrorURL:   url,
				CurrentVer:  currentVer,
				InstallPath: installTarget,
				Check:       check,
				Yes:         yes,
				Out:         os.Stdout,
			})
		},
	}
	c.Flags().BoolVar(&check, "check", false, "Report status only; don't download or build")
	c.Flags().BoolVarP(&yes, "yes", "y", false, "Skip confirmation")
	c.Flags().StringVar(&ref, "ref", "", "(--from-source only) branch or tag to update to")
	c.Flags().BoolVar(&fromSource, "from-source", false, "Build from a local repo checkout instead of the mirror")
	c.Flags().StringVar(&mirrorURL, "mirror", defaultMirrorURL, "Manifest URL (overrides INFRA_MIRROR_URL when set explicitly)")
	return c
}

func runGit(ctx context.Context, dir string, args ...string) error {
	cmd := exec.CommandContext(ctx, "git", args...)
	cmd.Dir = dir
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

func revParse(ctx context.Context, dir, ref string) string {
	cmd := exec.CommandContext(ctx, "git", "rev-parse", ref)
	cmd.Dir = dir
	out, err := cmd.Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

func currentBranch(ctx context.Context, dir string) string {
	cmd := exec.CommandContext(ctx, "git", "rev-parse", "--abbrev-ref", "HEAD")
	cmd.Dir = dir
	out, err := cmd.Output()
	if err != nil {
		return "master"
	}
	return strings.TrimSpace(string(out))
}

func countAheadBehind(ctx context.Context, dir, branch string) (ahead, behind int) {
	cmd := exec.CommandContext(ctx, "git", "rev-list", "--left-right", "--count",
		fmt.Sprintf("HEAD...origin/%s", branch))
	cmd.Dir = dir
	out, err := cmd.Output()
	if err != nil {
		return 0, 0
	}
	fields := strings.Fields(string(out))
	if len(fields) != 2 {
		return 0, 0
	}
	fmt.Sscanf(fields[0], "%d", &ahead)
	fmt.Sscanf(fields[1], "%d", &behind)
	return
}

func short(sha string) string {
	if len(sha) < 7 {
		return sha
	}
	return sha[:7]
}

func installPath() (string, error) {
	if exe, err := os.Executable(); err == nil {
		if resolved, err := filepath.EvalSymlinks(exe); err == nil {
			return resolved, nil
		}
		return exe, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".local", "bin", "infra"), nil
}

func buildLDFlags(root string) string {
	ver := gitDescribe(root)
	sha := gitShortSHA(root)
	return fmt.Sprintf("-X main.Version=%s -X main.Commit=%s", ver, sha)
}

func gitDescribe(dir string) string {
	cmd := exec.Command("git", "-C", dir, "describe", "--tags", "--always", "--dirty")
	out, err := cmd.Output()
	if err != nil {
		return "dev"
	}
	return strings.TrimSpace(string(out))
}

func gitShortSHA(dir string) string {
	cmd := exec.Command("git", "-C", dir, "rev-parse", "--short", "HEAD")
	out, err := cmd.Output()
	if err != nil {
		return "unknown"
	}
	return strings.TrimSpace(string(out))
}

type runFromSourceOpts struct {
	Check bool
	Yes   bool
	Ref   string
}

// runFromSource implements the historical "git pull && go build" update flow.
// Retained as a fallback for hosts with a repo checkout when the mirror is
// unreachable or for development.
func runFromSource(ctx context.Context, _ *cobra.Command, opts runFromSourceOpts) error {
	root, err := repo.Locate()
	if err != nil {
		return err
	}

	if err := runGit(ctx, root, "fetch", "--quiet"); err != nil {
		return fmt.Errorf("git fetch: %w", err)
	}

	branch := opts.Ref
	if branch == "" {
		branch = currentBranch(ctx, root)
	}
	remoteRef := "origin/" + branch

	local := revParse(ctx, root, "HEAD")
	remote := revParse(ctx, root, remoteRef)
	if local == "" || remote == "" {
		return fmt.Errorf("could not resolve git refs")
	}
	if local == remote {
		fmt.Printf("Up-to-date: %s is at %s\n", branch, short(local))
		return nil
	}

	ahead, behind := countAheadBehind(ctx, root, branch)
	fmt.Printf("Current: %s\nOrigin:  %s (behind %d, ahead %d)\n",
		short(local), short(remote), behind, ahead)
	if opts.Check {
		return nil
	}

	if !opts.Yes {
		ok, err := ui.Confirm("Pull and rebuild?")
		if err != nil {
			return err
		}
		if !ok {
			return nil
		}
	}

	if err := runGit(ctx, root, "pull", "--ff-only"); err != nil {
		return fmt.Errorf("git pull: %w", err)
	}

	installTarget, err := installPath()
	if err != nil {
		return err
	}
	tmp := installTarget + ".new"
	cliDir := filepath.Join(root, "cli")
	build := exec.CommandContext(ctx, "go", "build",
		"-ldflags", buildLDFlags(root),
		"-o", tmp,
		"./cmd/infra")
	build.Dir = cliDir
	build.Stdout = os.Stdout
	build.Stderr = os.Stderr
	if err := build.Run(); err != nil {
		_ = os.Remove(tmp)
		return fmt.Errorf("go build: %w", err)
	}
	verify := exec.CommandContext(ctx, tmp, "version")
	if out, err := verify.CombinedOutput(); err != nil {
		_ = os.Remove(tmp)
		return fmt.Errorf("verify new binary failed: %w\n%s", err, out)
	}
	if err := os.Rename(tmp, installTarget); err != nil {
		return fmt.Errorf("install: %w", err)
	}
	fmt.Printf("Updated %s\n", installTarget)
	return nil
}

type runFromMirrorOpts struct {
	MirrorURL   string
	CurrentVer  string
	InstallPath string
	Check       bool
	Yes         bool
	Out         io.Writer
}

// runFromMirror implements the default update flow: pull the manifest from the
// LAN mirror, verify sha256, atomic-rename over the running binary.
func runFromMirror(ctx context.Context, opts runFromMirrorOpts) error {
	if opts.Out == nil {
		opts.Out = os.Stdout
	}
	m, err := manifest.Fetch(ctx, opts.MirrorURL)
	if err != nil {
		return fmt.Errorf("mirror at %s unreachable: %w (use --from-source if you have a repo checkout)", opts.MirrorURL, err)
	}
	arch := runtime.GOOS + "/" + runtime.GOARCH
	bin, err := m.ForArch(arch)
	if err != nil {
		return err
	}
	fmt.Fprintf(opts.Out, "Current: %s\nMirror:  %s\n", opts.CurrentVer, m.Version)
	if !manifest.Newer(m.Version, opts.CurrentVer) {
		fmt.Fprintf(opts.Out, "Already on latest (%s).\n", m.Version)
		return nil
	}
	if opts.Check {
		return nil
	}
	if !opts.Yes {
		ok, err := ui.Confirm(fmt.Sprintf("Update %s → %s?", opts.CurrentVer, m.Version))
		if err != nil {
			return err
		}
		if !ok {
			return nil
		}
	}

	tmp := opts.InstallPath + ".new"
	if err := download(ctx, bin.URL, tmp); err != nil {
		return err
	}
	defer os.Remove(tmp) // no-op on success after rename

	got, err := sha256File(tmp)
	if err != nil {
		return err
	}
	if got != bin.SHA256 {
		return fmt.Errorf("downloaded binary failed checksum verification (got %s, want %s); existing binary unchanged", got, bin.SHA256)
	}
	if err := os.Chmod(tmp, 0755); err != nil {
		return fmt.Errorf("chmod: %w", err)
	}
	if err := os.Rename(tmp, opts.InstallPath); err != nil {
		return fmt.Errorf("install: %w", err)
	}
	fmt.Fprintf(opts.Out, "Done. infra is now %s.\n", m.Version)
	return nil
}

func download(ctx context.Context, url, dest string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return fmt.Errorf("download %s: %w", url, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("download %s: HTTP %d", url, resp.StatusCode)
	}
	f, err := os.OpenFile(dest, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0644)
	if err != nil {
		return err
	}
	if _, err := io.Copy(f, resp.Body); err != nil {
		_ = f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return fmt.Errorf("write %s: %w", dest, err)
	}
	return nil
}

func sha256File(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
