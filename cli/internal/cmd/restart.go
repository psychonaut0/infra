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

func newRestartCmd() *cobra.Command {
	var yes bool
	c := &cobra.Command{
		Use:   "restart <service>",
		Short: "Restart a service via docker compose",
		Args:  cobra.ExactArgs(1),
		RunE: func(_ *cobra.Command, args []string) error {
			root, err := repo.Locate()
			if err != nil {
				return err
			}
			idx, err := discover.Walk(root)
			if err != nil {
				return err
			}

			target := args[0]
			// Extract the pure service name if the caller used ct:svc form.
			svc := target
			if i := strings.Index(target, ":"); i >= 0 {
				svc = target[i+1:]
			}

			loc, err := idx.Resolve(target)
			if err != nil {
				return err
			}

			if !yes {
				ok, err := ui.Confirm(fmt.Sprintf("Restart %s on %s?", svc, loc.CT))
				if err != nil {
					return err
				}
				if !ok {
					fmt.Fprintln(os.Stderr, "Aborted.")
					return nil
				}
			}

			// /opt/stacks/<ct>/docker-compose.yml is the canonical compose
			// location inside each CT.
			composePath := fmt.Sprintf("/opt/stacks/%s/docker-compose.yml", loc.CT)
			remote := fmt.Sprintf("docker compose -f %s restart %s", composePath, svc)

			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
			defer cancel()
			return ssh.New().Interactive(ctx, loc.CT, remote)
		},
	}
	c.Flags().BoolVarP(&yes, "yes", "y", false, "Skip confirmation")
	return c
}
