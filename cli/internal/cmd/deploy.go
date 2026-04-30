package cmd

import (
	"context"
	"fmt"
	"os"
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
	c := &cobra.Command{
		Use:   "deploy <service|ct>",
		Short: "Pull images + up -d (service-level or whole-CT)",
		Args:  cobra.ExactArgs(1),
		RunE: func(_ *cobra.Command, args []string) error {
			idx, err := discover.Load(repo.Locate)
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

			composePath := fmt.Sprintf("/opt/stacks/%s/docker-compose.yml", ct)
			remote := ""
			if !noPull {
				remote += fmt.Sprintf("docker compose -f %s pull %s && ", composePath, svcArg)
			}
			remote += fmt.Sprintf("docker compose -f %s up -d %s", composePath, svcArg)

			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
			defer cancel()
			return ssh.New().Interactive(ctx, idx.SSHTarget(ct), remote)
		},
	}
	c.Flags().BoolVarP(&yes, "yes", "y", false, "Skip confirmation")
	c.Flags().BoolVar(&noPull, "no-pull", false, "Skip 'docker compose pull' step")
	return c
}
