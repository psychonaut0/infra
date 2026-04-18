package discover

import (
	"errors"
	"fmt"
	"strings"
)

// Sentinel errors for Resolve.
var (
	ErrNotFound  = errors.New("service not found")
	ErrAmbiguous = errors.New("service runs on multiple CTs")
)

// Resolve turns a user-provided target into a single ServiceLocation.
//
// Accepts two forms:
//   - "sonarr"            → unique match required
//   - "ct-media:sonarr"   → explicit CT:service, allowed for ambiguous services
//
// Returns ErrNotFound or ErrAmbiguous (wrapped) with a helpful message.
func (i *Index) Resolve(target string) (ServiceLocation, error) {
	ct := ""
	svc := target
	if idx := strings.Index(target, ":"); idx >= 0 {
		ct = target[:idx]
		svc = target[idx+1:]
	}

	locs, ok := i.Services[svc]
	if !ok {
		return ServiceLocation{}, fmt.Errorf("%w: %q. Try 'infra ls' for the service list", ErrNotFound, svc)
	}

	if ct == "" {
		if len(locs) == 1 {
			return locs[0], nil
		}
		cts := make([]string, len(locs))
		for n, l := range locs {
			cts[n] = l.CT
		}
		return ServiceLocation{}, fmt.Errorf(
			"%w: %q is on %s. Use 'ct-name:%s' to disambiguate",
			ErrAmbiguous, svc, strings.Join(cts, ", "), svc,
		)
	}

	for _, l := range locs {
		if l.CT == ct {
			return l, nil
		}
	}
	return ServiceLocation{}, fmt.Errorf("%w: %q not found on %s", ErrNotFound, svc, ct)
}
