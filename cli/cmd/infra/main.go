// Package main is the entrypoint for the `infra` CLI.
package main

import (
	"os"

	"github.com/psychonaut0/infra/cli/internal/cmd"
)

// Version metadata, populated at build time via -ldflags.
var (
	Version = "dev"
	Commit  = "unknown"
)

func main() {
	if err := cmd.Execute(cmd.BuildInfo{Version: Version, Commit: Commit}); err != nil {
		os.Exit(1)
	}
}
