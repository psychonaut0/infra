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
