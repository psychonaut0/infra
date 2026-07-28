package tunnel

import (
	"fmt"
	"strings"
)

// UnifiedDiff returns a human-readable line diff of want vs got, or "" when
// they are byte-identical.
//
// This is deliberately a simple line-by-line comparison rather than a real LCS
// diff: the inputs are two renderings of the same deterministic template, so
// differences are small and positional. It also avoids adding a dependency.
func UnifiedDiff(want, got []byte, wantName, gotName string) string {
	if string(want) == string(got) {
		return ""
	}
	wantLines := splitLines(want)
	gotLines := splitLines(got)

	var b strings.Builder
	fmt.Fprintf(&b, "--- %s\n+++ %s\n", wantName, gotName)
	n := len(wantLines)
	if len(gotLines) > n {
		n = len(gotLines)
	}
	for i := 0; i < n; i++ {
		var w, g string
		var haveW, haveG bool
		if i < len(wantLines) {
			w, haveW = wantLines[i], true
		}
		if i < len(gotLines) {
			g, haveG = gotLines[i], true
		}
		switch {
		case haveW && haveG && w == g:
			// unchanged: show as context
			fmt.Fprintf(&b, " %s\n", w)
		default:
			if haveW {
				fmt.Fprintf(&b, "-%s\n", w)
			}
			if haveG {
				fmt.Fprintf(&b, "+%s\n", g)
			}
		}
	}
	return b.String()
}

func splitLines(b []byte) []string {
	if len(b) == 0 {
		return nil
	}
	s := strings.TrimSuffix(string(b), "\n")
	if s == "" {
		return nil
	}
	return strings.Split(s, "\n")
}
