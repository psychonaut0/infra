// Package cmd holds the cobra command tree for the infra CLI.
package cmd

import (
	"fmt"

	"github.com/spf13/cobra"
)

var (
	version string
	commit  string
)

// Execute builds the command tree and runs it.
func Execute(v, c string) error {
	version = v
	commit = c

	root := &cobra.Command{
		Use:   "infra",
		Short: "Homelab infrastructure CLI",
		Long:  "infra wraps SSH + docker compose operations across the homelab.",
	}
	root.AddCommand(newVersionCmd())
	return root.Execute()
}

func newVersionCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "version",
		Short: "Print version information",
		Run: func(cmd *cobra.Command, args []string) {
			fmt.Printf("infra %s (%s)\n", version, commit)
		},
	}
}
