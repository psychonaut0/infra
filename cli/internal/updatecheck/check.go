package updatecheck

import (
	"context"
	"fmt"
	"io"
	"os"
	"time"

	"github.com/psychonaut0/infra/cli/internal/manifest"
)

// Checker performs a passive, cached version check against the mirror.
//
// Lifecycle:
//   - Refresh(ctx) (typically in a goroutine from PersistentPreRun) updates
//     the cache file if the existing entry is older than TTL.
//   - Footer() (called from PersistentPostRun) reads the cache and, if
//     enabled and a newer version is known, prints a one-line stderr footer.
type Checker struct {
	MirrorURL      string
	CachePath      string
	TTL            time.Duration
	CurrentVersion string
	Enabled        bool
	Out            io.Writer
	Now            func() time.Time
}

func (c *Checker) now() time.Time {
	if c.Now != nil {
		return c.Now()
	}
	return time.Now()
}

// Refresh fetches the manifest if the cache is stale and writes the new
// entry. All errors are silent — the check must never disrupt the user's
// command.
func (c *Checker) Refresh(ctx context.Context) {
	if !c.Enabled {
		return
	}
	if e, err := readCache(c.CachePath); err == nil {
		if !stale(e.LastCheckAt, c.TTL, c.now()) {
			return
		}
	}
	m, err := manifest.Fetch(ctx, c.MirrorURL)
	if err != nil {
		return
	}
	_ = writeCache(c.CachePath, cacheEntry{
		LastCheckAt:   c.now(),
		LatestVersion: m.Version,
	})
}

// Footer prints a single-line "update available" notice to c.Out if a newer
// version has been observed in cache. No-op when disabled, when the cache is
// missing, or when the cached version is not newer than CurrentVersion.
func (c *Checker) Footer() {
	if !c.Enabled || c.Out == nil {
		return
	}
	e, err := readCache(c.CachePath)
	if err != nil {
		return
	}
	if !manifest.Newer(e.LatestVersion, c.CurrentVersion) {
		return
	}
	fmt.Fprintf(c.Out, "[infra update available: %s → %s — run 'infra update']\n",
		c.CurrentVersion, e.LatestVersion)
}

// New returns a Checker pre-configured with default cache path and the
// suppression rules from the spec (env opt-out, dev build, non-TTY stderr).
func New(currentVersion string) *Checker {
	c := &Checker{
		MirrorURL:      "http://infra-bin.lan/manifest.json",
		TTL:            24 * time.Hour,
		CurrentVersion: currentVersion,
		Out:            os.Stderr,
		Enabled:        defaultEnabled(currentVersion),
	}
	if p, err := cachePath(); err == nil {
		c.CachePath = p
	}
	if env := os.Getenv("INFRA_MIRROR_URL"); env != "" {
		c.MirrorURL = env
	}
	return c
}

func defaultEnabled(currentVersion string) bool {
	if os.Getenv("INFRA_NO_UPDATE_CHECK") == "1" {
		return false
	}
	if currentVersion == "dev" || currentVersion == "" {
		return false
	}
	return isTerminal(os.Stderr)
}
