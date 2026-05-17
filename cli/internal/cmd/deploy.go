package cmd

import (
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/psychonaut0/infra/cli/internal/discover"
	"github.com/psychonaut0/infra/cli/internal/repo"
	"github.com/psychonaut0/infra/cli/internal/ssh"
	"github.com/psychonaut0/infra/cli/internal/ui"
	"github.com/spf13/cobra"
)

func newDeployCmd() *cobra.Command {
	var yes bool
	var noPull bool
	var noSync bool
	c := &cobra.Command{
		Use:   "deploy <service|ct>",
		Short: "Sync stack files + pull images + up -d (service-level or whole-CT)",
		Args:  cobra.ExactArgs(1),
		RunE: func(_ *cobra.Command, args []string) error {
			repoRoot, err := repo.Locate()
			if err != nil {
				return err
			}
			idx, err := discover.Load(func() (string, error) { return repoRoot, nil })
			if err != nil {
				return err
			}

			target := args[0]
			// Detect "ct-name" form: starts with "ct-" AND appears in the
			// index as a known CT.
			ctNames := map[string]bool{}
			for _, locs := range idx.Services {
				for _, l := range locs {
					ctNames[l.CT] = true
				}
			}

			var ct, svcArg string
			switch {
			case ctNames[target]:
				ct = target
				svcArg = "" // whole-stack
			default:
				svc := target
				if i := strings.Index(target, ":"); i >= 0 {
					svc = target[i+1:]
				}
				loc, err := idx.Resolve(target)
				if err != nil {
					return err
				}
				ct = loc.CT
				svcArg = svc
			}

			what := "all services"
			if svcArg != "" {
				what = svcArg
			}
			if !yes {
				ok, err := ui.Confirm(fmt.Sprintf("Deploy %s on %s?", what, ct))
				if err != nil {
					return err
				}
				if !ok {
					fmt.Fprintln(os.Stderr, "Aborted.")
					return nil
				}
			}

			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
			defer cancel()

			sshTarget := idx.SSHTarget(ct)
			runner := ssh.New()

			if !noSync {
				if err := syncStack(ctx, runner, sshTarget, ct, repoRoot); err != nil {
					return fmt.Errorf("sync stack files: %w", err)
				}
			}

			composePath := fmt.Sprintf("/opt/stacks/%s/docker-compose.yml", ct)
			remote := ""
			if !noPull {
				remote += fmt.Sprintf("docker compose -f %s pull %s && ", composePath, svcArg)
			}
			// --force-recreate is required so containers pick up bind-mounted
			// config changes (the compose file itself rarely changes, so
			// without it `up -d` would no-op).
			remote += fmt.Sprintf("docker compose -f %s up -d --force-recreate %s", composePath, svcArg)

			return runner.Interactive(ctx, sshTarget, remote)
		},
	}
	c.Flags().BoolVarP(&yes, "yes", "y", false, "Skip confirmation")
	c.Flags().BoolVar(&noPull, "no-pull", false, "Skip 'docker compose pull' step")
	c.Flags().BoolVar(&noSync, "no-sync", false, "Skip syncing stacks/<ct>/ to /opt/stacks/<ct>/")
	return c
}

// syncStack tars stacks/<ct>/ from the local repo and untars it into
// /opt/stacks/ on the target host, so config changes in the repo reach
// the deployed bind-mounts. .env files are excluded — they are
// server-managed and would clobber live secrets.
func syncStack(ctx context.Context, runner *ssh.Runner, target, ct, repoRoot string) error {
	stacksDir := filepath.Join(repoRoot, "stacks")
	if _, err := os.Stat(filepath.Join(stacksDir, ct)); err != nil {
		return fmt.Errorf("local stack dir not found: %w", err)
	}

	pr, pw := io.Pipe()
	tarCmd := exec.CommandContext(ctx, "tar",
		"-C", stacksDir,
		"--exclude=.env",
		"--exclude=.env.local",
		"-cf", "-", ct)
	tarCmd.Stdout = pw
	tarCmd.Stderr = os.Stderr

	if err := tarCmd.Start(); err != nil {
		return fmt.Errorf("start local tar: %w", err)
	}

	tarErrCh := make(chan error, 1)
	go func() {
		err := tarCmd.Wait()
		pw.CloseWithError(err)
		tarErrCh <- err
	}()

	if err := runner.Stream(ctx, target, "tar -C /opt/stacks -xf -", pr, nil, os.Stderr); err != nil {
		return fmt.Errorf("remote untar: %w", err)
	}
	if err := <-tarErrCh; err != nil {
		return fmt.Errorf("local tar: %w", err)
	}
	return nil
}
