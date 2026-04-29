package cmd

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"strings"
	"syscall"

	"github.com/psychonaut0/infra/cli/internal/discover"
	"github.com/psychonaut0/infra/cli/internal/repo"
	"github.com/psychonaut0/infra/cli/internal/ssh"
	"github.com/spf13/cobra"
)

func newLogsCmd() *cobra.Command {
	var lines int
	var noFollow bool
	var since string
	c := &cobra.Command{
		Use:   "logs <service>",
		Short: "Tail logs for a service (follow mode by default)",
		Args:  cobra.ExactArgs(1),
		RunE: func(_ *cobra.Command, args []string) error {
			idx, err := discover.Load(repo.Locate)
			if err != nil {
				return err
			}
			loc, err := idx.Resolve(args[0])
			if err != nil {
				return err
			}

			// If the user passed "ct-name:service", strip the prefix for the
			// docker logs argument (the container name is just the service).
			containerName := args[0]
			if i := strings.Index(containerName, ":"); i >= 0 {
				containerName = containerName[i+1:]
			}

			remote := fmt.Sprintf("docker logs --tail %d", lines)
			if !noFollow {
				remote += " --follow"
			}
			if since != "" {
				remote += fmt.Sprintf(" --since %q", since)
			}
			remote += " " + containerName

			// Handle Ctrl-C: cancel the context → close the SSH → remote docker stops.
			ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
			defer cancel()

			runner := ssh.New()
			err = runner.Interactive(ctx, loc.CT, remote)
			// ssh returns non-zero on Ctrl-C — treat as clean exit if context was cancelled.
			if err != nil && ctx.Err() != nil {
				return nil
			}
			return err
		},
	}
	c.Flags().IntVarP(&lines, "lines", "n", 100, "Number of trailing lines")
	c.Flags().BoolVar(&noFollow, "no-follow", false, "Don't follow new output")
	c.Flags().StringVar(&since, "since", "", "Show logs since duration/time (e.g. 1h, 2026-04-17)")
	return c
}
