package tunnel

import (
	"fmt"
	"strings"
)

// maxLCSLines is a deliberate safety valve, not a limitation of the LCS
// algorithm itself: the O(n*m) table below is fine for the tens-of-lines
// tunnel configs this package actually diffs, but a table sized off an
// unexpectedly huge input could allocate gigabytes. If either side exceeds
// this many lines, UnifiedDiff falls back to the cheap positional comparison
// instead of building the table.
const maxLCSLines = 5000

// UnifiedDiff returns a human-readable line diff of want vs got, or "" when
// they are byte-identical.
//
// Lines are matched by content via a longest-common-subsequence (LCS) diff,
// not by position: an insertion or deletion near the top of one side is
// recognised as exactly that, rather than cascading into every later line
// being reported as changed. That distinction matters here because the most
// common real edit — adding one ingress hostname — shifts every following
// line by one; a positional comparison would report the whole file as
// rewritten and hide the one line that actually changed.
//
// See maxLCSLines for the one exception: on inputs large enough that the
// O(n*m) table would be wasteful, UnifiedDiff falls back to a plain
// positional comparison.
func UnifiedDiff(want, got []byte, wantName, gotName string) string {
	if string(want) == string(got) {
		return ""
	}
	wantLines := splitLines(want)
	gotLines := splitLines(got)

	var b strings.Builder
	fmt.Fprintf(&b, "--- %s\n+++ %s\n", wantName, gotName)

	if len(wantLines) > maxLCSLines || len(gotLines) > maxLCSLines {
		writePositionalDiff(&b, wantLines, gotLines)
	} else {
		writeLCSDiff(&b, wantLines, gotLines)
	}
	return b.String()
}

// writeLCSDiff writes a longest-common-subsequence line diff of wantLines vs
// gotLines to b: unchanged lines as context (" "), lines only in wantLines as
// removed ("-"), lines only in gotLines as added ("+").
//
// This is the textbook DP formulation: lcs[i][j] holds the length of the
// longest common subsequence of wantLines[i:] and gotLines[j:], built from
// the bottom-right corner up. Backtracking from lcs[0][0] forward — following
// whichever neighbour (right or down) preserves the LCS length — reconstructs
// the actual diff. The table is O(n*m) time and space, which is why
// UnifiedDiff only calls this below maxLCSLines.
func writeLCSDiff(b *strings.Builder, wantLines, gotLines []string) {
	n, m := len(wantLines), len(gotLines)
	lcs := make([][]int, n+1)
	for i := range lcs {
		lcs[i] = make([]int, m+1)
	}
	for i := n - 1; i >= 0; i-- {
		for j := m - 1; j >= 0; j-- {
			switch {
			case wantLines[i] == gotLines[j]:
				lcs[i][j] = lcs[i+1][j+1] + 1
			case lcs[i+1][j] >= lcs[i][j+1]:
				lcs[i][j] = lcs[i+1][j]
			default:
				lcs[i][j] = lcs[i][j+1]
			}
		}
	}

	i, j := 0, 0
	for i < n && j < m {
		switch {
		case wantLines[i] == gotLines[j]:
			fmt.Fprintf(b, " %s\n", wantLines[i])
			i++
			j++
		case lcs[i+1][j] >= lcs[i][j+1]:
			fmt.Fprintf(b, "-%s\n", wantLines[i])
			i++
		default:
			fmt.Fprintf(b, "+%s\n", gotLines[j])
			j++
		}
	}
	for ; i < n; i++ {
		fmt.Fprintf(b, "-%s\n", wantLines[i])
	}
	for ; j < m; j++ {
		fmt.Fprintf(b, "+%s\n", gotLines[j])
	}
}

// writePositionalDiff is the maxLCSLines safety-valve fallback: it compares
// wantLines and gotLines index by index, exactly like a naive diff would,
// without allocating anything proportional to n*m. It is intentionally not
// used for normal-sized input — see UnifiedDiff's doc comment.
func writePositionalDiff(b *strings.Builder, wantLines, gotLines []string) {
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
			fmt.Fprintf(b, " %s\n", w)
		default:
			if haveW {
				fmt.Fprintf(b, "-%s\n", w)
			}
			if haveG {
				fmt.Fprintf(b, "+%s\n", g)
			}
		}
	}
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
