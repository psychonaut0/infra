// Package cmd holds the cobra command tree for the infra CLI.
package cmd

import (
	"fmt"

	"github.com/spf13/cobra"
)

// BuildInfo holds metadata injected at build time via -ldflags.
type BuildInfo struct {
	Version string
	Commit  string
}

// Execute builds the command tree and runs it.
func Execute(info BuildInfo) error {
	root := &cobra.Command{
		Use:   "infra",
		Short: "Homelab infrastructure CLI",
		Long:  "infra wraps SSH + docker compose operations across the homelab.",
		Annotations: map[string]string{
			"version": info.Version,
			"commit":  info.Commit,
		},
		SilenceUsage: true,
	}
	root.AddCommand(newVersionCmd())
	root.AddCommand(newLsCmd())
	root.AddCommand(newStatusCmd())
	root.AddCommand(newLogsCmd())
	root.AddCommand(newRestartCmd())
	root.AddCommand(newDeployCmd())
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
