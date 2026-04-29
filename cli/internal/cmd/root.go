// Package cmd holds the cobra command tree for the infra CLI.
package cmd

import (
	"context"
	"fmt"
	"sync"
	"time"

	"github.com/psychonaut0/infra/cli/internal/updatecheck"
	"github.com/spf13/cobra"
)

// BuildInfo holds metadata injected at build time via -ldflags.
type BuildInfo struct {
	Version string
	Commit  string
}

// Execute builds the command tree and runs it.
func Execute(info BuildInfo) error {
	checker := updatecheck.New(info.Version)
	var wg sync.WaitGroup

	root := &cobra.Command{
		Use:   "infra",
		Short: "Homelab infrastructure CLI",
		Long:  "infra wraps SSH + docker compose operations across the homelab.",
		Annotations: map[string]string{
			"version": info.Version,
			"commit":  info.Commit,
		},
		SilenceUsage: true,
		PersistentPreRun: func(cmd *cobra.Command, _ []string) {
			// Skip the network call on update commands and on -h/--help.
			if cmd.Name() == "update" || cmd.Name() == "help" {
				return
			}
			wg.Add(1)
			go func() {
				defer wg.Done()
				ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
				defer cancel()
				checker.Refresh(ctx)
			}()
		},
		PersistentPostRun: func(_ *cobra.Command, _ []string) {
			wg.Wait()
			checker.Footer()
		},
	}
	root.AddCommand(newVersionCmd())
	root.AddCommand(newLsCmd())
	root.AddCommand(newStatusCmd())
	root.AddCommand(newLogsCmd())
	root.AddCommand(newRestartCmd())
	root.AddCommand(newDeployCmd())
	root.AddCommand(newCtCmd())
	root.AddCommand(newUpdateCmd())
	return root.Execute()
}

func newVersionCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "version",
		Short: "Print version information",
		Run: func(cmd *cobra.Command, _ []string) {
			a := cmd.Root().Annotations
			fmt.Printf("infra %s (%s)\n", a["version"], a["commit"])
		},
	}
}
