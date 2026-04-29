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
	// PersistentPostRun runs only on RunE success; on error the goroutine may
	// still be in flight when Execute returns. That's fine — the writeCache
	// uses tmp+rename so the cache file is never half-written, and the OS
	// reaps the goroutine when main exits.
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
			// Skip the network call when the user is already running update (the
			// command does its own check) or invoking `infra help <cmd>` (cobra short-
			// circuits `--help`/`-h` before PreRun fires, so this only catches the
			// explicit "help" subcommand).
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
