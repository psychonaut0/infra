package cmd

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/psychonaut0/infra/cli/internal/repo"
	"github.com/psychonaut0/infra/cli/internal/ui"
	"github.com/spf13/cobra"
)

func newUpdateCmd() *cobra.Command {
	var check bool
	var yes bool
	var ref string
	c := &cobra.Command{
		Use:   "update",
		Short: "Update the infra binary (git pull + rebuild + install)",
		RunE: func(_ *cobra.Command, _ []string) error {
			root, err := repo.Locate()
			if err != nil {
				return err
			}

			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
			defer cancel()

			// Fetch latest refs.
			if err := runGit(ctx, root, "fetch", "--quiet"); err != nil {
				return fmt.Errorf("git fetch: %w", err)
			}

			// Compare HEAD to origin/<ref>.
			branch := ref
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
			if check {
				return nil
			}

			if !yes {
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

			// Build new binary next to the current one, then atomic mv.
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

			// Sanity: the fresh binary runs.
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
		},
	}
	c.Flags().BoolVar(&check, "check", false, "Report status only; don't pull or build")
	c.Flags().BoolVarP(&yes, "yes", "y", false, "Skip confirmation")
	c.Flags().StringVar(&ref, "ref", "", "Branch or tag to update to (default: current branch)")
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
