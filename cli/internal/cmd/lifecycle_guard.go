package cmd

import (
	"errors"
	"fmt"
	"strings"

	"github.com/psychonaut0/infra/cli/internal/discover"
	"github.com/psychonaut0/infra/cli/internal/repo"
	"github.com/spf13/cobra"
)

// ErrGuardedLifecycle is returned when deploy or restart targets a CT whose
// containers may only be started, stopped or recreated through that CT's own
// guarded lifecycle.
var ErrGuardedLifecycle = errors.New("guarded lifecycle")

// guardedLifecycles maps a CT name to the lifecycle that owns it. The generic
// `docker compose up/restart` that deploy and restart run would bypass that
// lifecycle's holds, so both commands refuse every target that can reach the
// CT. The set is keyed by CT, not by service: any service a compose file (or a
// regenerated fleet snapshot) places on the CT is covered automatically.
// Discovery is not filtered, so status, ls and logs keep seeing the CT.
var guardedLifecycles = map[string]string{
	"ct-animus": "animus-stack.service; see stacks/ct-animus/ops/README.md",
}

// withLifecycleGuard adds the guarded-lifecycle refusal to c as a PreRunE, so
// it runs after argument validation and before any prompt, file sync or SSH.
// An existing PreRunE or PreRun is preserved and runs after the check.
func withLifecycleGuard(c *cobra.Command) *cobra.Command {
	prevE, prev := c.PreRunE, c.PreRun
	c.PreRun = nil
	c.PreRunE = func(cmd *cobra.Command, args []string) error {
		load := func() (*discover.Index, error) { return discover.Load(repo.Locate) }
		if err := checkLifecycleGuard(cmd.Name(), args, load); err != nil {
			return err
		}
		if prevE != nil {
			return prevE(cmd, args)
		}
		if prev != nil {
			prev(cmd, args)
		}
		return nil
	}
	return c
}

// checkLifecycleGuard refuses verb on any target that can reach a guarded CT.
// The fleet index is loaded only when a bare service name has to be resolved;
// a load failure refuses too, since the command could not resolve it either.
func checkLifecycleGuard(verb string, targets []string, load func() (*discover.Index, error)) error {
	var idx *discover.Index
	for _, target := range targets {
		ct, needsIndex := guardedCTByName(target)
		if ct == "" && needsIndex {
			if idx == nil {
				var err error
				if idx, err = load(); err != nil {
					return err
				}
			}
			ct = guardedCTByService(serviceName(target), idx)
		}
		if ct != "" {
			return fmt.Errorf("%w: infra %s refuses %q because it reaches %s. "+
				"Start, stop and recreate %s only through its guarded lifecycle (%s). "+
				"infra status, ls and logs still work",
				ErrGuardedLifecycle, verb, target, ct, ct, guardedLifecycles[ct])
		}
	}
	return nil
}

// guardedCTByName checks the forms that name a CT directly: the whole-CT
// target ("ct-animus") and the "ct:svc" form. It reports whether the target
// is instead a bare service name (including ":svc", which Resolve treats as
// unqualified) that needs the fleet index.
func guardedCTByName(target string) (ct string, needsIndex bool) {
	if _, ok := guardedLifecycles[target]; ok {
		return target, false
	}
	ctPart, _, qualified := strings.Cut(target, ":")
	if qualified && ctPart != "" {
		if _, ok := guardedLifecycles[ctPart]; ok {
			return ctPart, false
		}
		return "", false
	}
	return "", true
}

// guardedCTByService returns the guarded CT that hosts svc, if any. Every
// location counts, so a name shared with another CT is refused rather than
// left to the command's own ambiguity handling; "other-ct:svc" still works.
func guardedCTByService(svc string, idx *discover.Index) string {
	if _, ok := guardedLifecycles[svc]; ok {
		return svc
	}
	for _, loc := range idx.Services[svc] {
		if _, ok := guardedLifecycles[loc.CT]; ok {
			return loc.CT
		}
	}
	return ""
}

// serviceName strips an optional "ct:" prefix, matching Resolve.
func serviceName(target string) string {
	if _, svc, ok := strings.Cut(target, ":"); ok {
		return svc
	}
	return target
}
