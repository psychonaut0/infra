package tunnel

import (
	"strings"
	"testing"
)

func TestUnifiedDiff_Identical(t *testing.T) {
	b := []byte("a\nb\nc\n")
	if d := UnifiedDiff(b, b, "repo", "live"); d != "" {
		t.Errorf("identical input must produce an empty diff, got:\n%s", d)
	}
}

func TestUnifiedDiff_ShowsAddedAndRemoved(t *testing.T) {
	repoSide := []byte("keep\nold\ntail\n")
	liveSide := []byte("keep\nnew\ntail\n")
	d := UnifiedDiff(repoSide, liveSide, "repo", "live")
	if !strings.Contains(d, "-old") {
		t.Errorf("missing removed line marker:\n%s", d)
	}
	if !strings.Contains(d, "+new") {
		t.Errorf("missing added line marker:\n%s", d)
	}
	if !strings.Contains(d, "repo") || !strings.Contains(d, "live") {
		t.Errorf("diff should label both sides:\n%s", d)
	}
}

func TestUnifiedDiff_HandlesLengthChange(t *testing.T) {
	d := UnifiedDiff([]byte("one\n"), []byte("one\ntwo\nthree\n"), "repo", "live")
	if !strings.Contains(d, "+two") || !strings.Contains(d, "+three") {
		t.Errorf("added trailing lines missing:\n%s", d)
	}
}

func TestUnifiedDiff_HandlesEmptySide(t *testing.T) {
	d := UnifiedDiff(nil, []byte("only\n"), "repo", "live")
	if !strings.Contains(d, "+only") {
		t.Errorf("expected the live line as an addition:\n%s", d)
	}
}

// countLinePrefix counts diff body lines (i.e. excluding the "--- "/"+++ "
// header pair) that start with prefix.
func countLinePrefix(d, prefix string) int {
	n := 0
	for _, line := range strings.Split(d, "\n") {
		if strings.HasPrefix(line, "---") || strings.HasPrefix(line, "+++") {
			continue
		}
		if strings.HasPrefix(line, prefix) {
			n++
		}
	}
	return n
}

// TestUnifiedDiff_InsertionAtTop is the case a positional line-by-line
// comparison gets wrong: repo is [a,b,c], live inserts one line before all of
// them. Under positional comparison every line shifts and is reported as
// both removed and re-added; a content-aware (LCS) diff reports exactly the
// one inserted line and recognises a, b, c as unchanged context.
func TestUnifiedDiff_InsertionAtTop(t *testing.T) {
	repoSide := []byte("a\nb\nc\n")
	liveSide := []byte("x\na\nb\nc\n")
	d := UnifiedDiff(repoSide, liveSide, "repo", "live")

	if got := countLinePrefix(d, "+"); got != 1 {
		t.Errorf("expected exactly one added line, got %d:\n%s", got, d)
	}
	if got := countLinePrefix(d, "-"); got != 0 {
		t.Errorf("expected zero removed lines, got %d:\n%s", got, d)
	}
	if !strings.Contains(d, "+x") {
		t.Errorf("missing the inserted line as an addition:\n%s", d)
	}
	for _, want := range []string{" a", " b", " c"} {
		if !strings.Contains(d, want) {
			t.Errorf("expected %q to survive as context:\n%s", want, d)
		}
	}
}

// TestUnifiedDiff_InsertionInMiddle inserts a line between existing,
// unchanged lines rather than at an edge, to make sure the LCS diff isolates
// the insertion regardless of where it lands.
func TestUnifiedDiff_InsertionInMiddle(t *testing.T) {
	repoSide := []byte("a\nb\nc\n")
	liveSide := []byte("a\nx\nb\nc\n")
	d := UnifiedDiff(repoSide, liveSide, "repo", "live")

	if got := countLinePrefix(d, "+"); got != 1 {
		t.Errorf("expected exactly one added line, got %d:\n%s", got, d)
	}
	if got := countLinePrefix(d, "-"); got != 0 {
		t.Errorf("expected zero removed lines, got %d:\n%s", got, d)
	}
	if !strings.Contains(d, "+x") {
		t.Errorf("missing the inserted line as an addition:\n%s", d)
	}
	for _, want := range []string{" a", " b", " c"} {
		if !strings.Contains(d, want) {
			t.Errorf("expected %q to survive as context:\n%s", want, d)
		}
	}
}

// TestUnifiedDiff_DeletionFromMiddle is the mirror image: a line removed from
// the middle should show up as exactly one "-" line, with the rest surviving
// as context, not as a wholesale rewrite from that point on.
func TestUnifiedDiff_DeletionFromMiddle(t *testing.T) {
	repoSide := []byte("a\nb\nc\n")
	liveSide := []byte("a\nc\n")
	d := UnifiedDiff(repoSide, liveSide, "repo", "live")

	if got := countLinePrefix(d, "+"); got != 0 {
		t.Errorf("expected zero added lines, got %d:\n%s", got, d)
	}
	if got := countLinePrefix(d, "-"); got != 1 {
		t.Errorf("expected exactly one removed line, got %d:\n%s", got, d)
	}
	if !strings.Contains(d, "-b") {
		t.Errorf("missing the deleted line as a removal:\n%s", d)
	}
	for _, want := range []string{" a", " c"} {
		if !strings.Contains(d, want) {
			t.Errorf("expected %q to survive as context:\n%s", want, d)
		}
	}
}

// TestUnifiedDiff_RealisticIngressInsertion mirrors the shape of the actual
// data these functions diff: rendered ingress.yml has one "- hostname: ..."
// block per rule plus a trailing catch-all. Adding a new hostname is a
// two-line insertion before the catch-all — this asserts the diff isolates
// that block instead of rewriting every rule that follows it.
func TestUnifiedDiff_RealisticIngressInsertion(t *testing.T) {
	repoSide := []byte(`ingress:
  - hostname: foo.example.test
    service: http://192.168.3.11:8080
  - hostname: bar.example.test
    service: http://192.168.3.12:3000
  - hostname: baz.example.test
    service: http://192.168.3.13:9000
  - service: http_status:404
`)
	liveSide := []byte(`ingress:
  - hostname: foo.example.test
    service: http://192.168.3.11:8080
  - hostname: bar.example.test
    service: http://192.168.3.12:3000
  - hostname: baz.example.test
    service: http://192.168.3.13:9000
  - hostname: qux.example.test
    service: http://192.168.3.14:9090
  - service: http_status:404
`)
	d := UnifiedDiff(repoSide, liveSide, "repo", "live")

	if got := countLinePrefix(d, "-"); got != 0 {
		t.Errorf("expected zero removed lines — nothing was deleted, got %d:\n%s", got, d)
	}
	if got := countLinePrefix(d, "+"); got != 2 {
		t.Errorf("expected exactly the 2 added lines for the new hostname block, got %d:\n%s", got, d)
	}
	for _, want := range []string{
		"+  - hostname: qux.example.test",
		"+    service: http://192.168.3.14:9090",
	} {
		if !strings.Contains(d, want) {
			t.Errorf("missing added line %q:\n%s", want, d)
		}
	}
	for _, want := range []string{
		"  - hostname: foo.example.test",
		"  - hostname: bar.example.test",
		"  - hostname: baz.example.test",
		"  - service: http_status:404",
	} {
		if !strings.Contains(d, " "+want) {
			t.Errorf("expected unchanged rule %q to survive as context:\n%s", want, d)
		}
	}
	if strings.Contains(d, "-  - hostname: foo.example.test") ||
		strings.Contains(d, "-  - hostname: bar.example.test") ||
		strings.Contains(d, "-  - hostname: baz.example.test") {
		t.Errorf("existing hostname blocks must not be reported as removed:\n%s", d)
	}
}
