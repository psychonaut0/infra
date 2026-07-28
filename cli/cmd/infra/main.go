// Package main is the entrypoint for the `infra` CLI.
package main

import (
	"errors"
	"os"

	"github.com/psychonaut0/infra/cli/internal/cmd"
)

// Version metadata, populated at build time via -ldflags.
var (
	Version = "dev"
	Commit  = "unknown"
)

func main() {
	err := cmd.Execute(cmd.BuildInfo{Version: Version, Commit: Commit})
	if err == nil {
		return
	}
	// `infra tunnel diff` signals drift with a distinct exit code so a
	// scheduled caller can tell "drifted" from "the check itself failed".
	if errors.Is(err, cmd.ErrDrift) {
		os.Exit(2)
	}
	os.Exit(1)
}
