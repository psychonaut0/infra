// Package ssh is a thin os/exec wrapper around the system ssh binary.
// It assumes the user has ~/.ssh/config set up for host aliases and keys.
package ssh

import (
	"context"
	"io"
	"os"
	"os/exec"
)

// Runner executes commands on remote hosts via system ssh.
type Runner struct {
	// ExtraArgs are appended to every ssh invocation (e.g. "-q" for quiet).
	ExtraArgs []string
}

// New returns a Runner with sensible defaults for backup/batch use.
func New() *Runner {
	return &Runner{
		ExtraArgs: []string{"-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"},
	}
}

// Output runs `ssh <host> <cmd>` and returns stdout bytes. Stderr is captured
// and attached to the error.
func (r *Runner) Output(ctx context.Context, host, remoteCmd string) ([]byte, error) {
	args := append([]string{}, r.ExtraArgs...)
	args = append(args, host, remoteCmd)
	cmd := exec.CommandContext(ctx, "ssh", args...)
	return cmd.Output()
}

// Stream runs `ssh <host> <cmd>` with stdin/stdout/stderr wired directly to
// the caller's streams. Used for interactive commands like `docker logs -f`.
func (r *Runner) Stream(ctx context.Context, host, remoteCmd string, stdin io.Reader, stdout, stderr io.Writer) error {
	args := append([]string{}, r.ExtraArgs...)
	args = append(args, host, remoteCmd)
	cmd := exec.CommandContext(ctx, "ssh", args...)
	cmd.Stdin = stdin
	cmd.Stdout = stdout
	cmd.Stderr = stderr
	return cmd.Run()
}

// Interactive runs `ssh -t <host> <cmd>` with os.Stdin/Stdout/Stderr wired
// through (TTY allocated). Used for `infra logs -f` etc.
func (r *Runner) Interactive(ctx context.Context, host, remoteCmd string) error {
	args := append([]string{"-t"}, r.ExtraArgs...)
	args = append(args, host, remoteCmd)
	cmd := exec.CommandContext(ctx, "ssh", args...)
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}
