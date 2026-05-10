package dns

import "sort"

// Drift describes the changes needed to bring live state to desired state.
type Drift struct {
	ToAdd    []Record
	ToRemove []Record
	ToChange []DriftChange
}

// DriftChange holds the old (live) and new (desired) form of a record whose
// IP changed.
type DriftChange struct {
	Old Record
	New Record
}

// ComputeDesired returns the canonical Record set: every distinct Caddy
// hostname pointing at ctMgmtIP, plus every direct entry pointing at its
// own IP. Sorted by hostname.
func ComputeDesired(blocks []Block, extras []ExtraEntry, ctMgmtIP string) []Record {
	seen := map[string]Record{}
	for _, b := range blocks {
		seen[b.Hostname] = Record{Hostname: b.Hostname, IP: ctMgmtIP}
	}
	for _, e := range extras {
		seen[e.Name] = Record{Hostname: e.Name, IP: e.IP}
	}
	out := make([]Record, 0, len(seen))
	for _, r := range seen {
		out = append(out, r)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Hostname < out[j].Hostname })
	return out
}

// ComputeDrift compares two record lists keyed by hostname.
func ComputeDrift(desired, live []Record) Drift {
	d := Drift{}
	desiredByHost := map[string]Record{}
	for _, r := range desired {
		desiredByHost[r.Hostname] = r
	}
	liveByHost := map[string]Record{}
	for _, r := range live {
		liveByHost[r.Hostname] = r
	}
	for h, want := range desiredByHost {
		got, ok := liveByHost[h]
		if !ok {
			d.ToAdd = append(d.ToAdd, want)
			continue
		}
		if got.IP != want.IP {
			d.ToChange = append(d.ToChange, DriftChange{Old: got, New: want})
		}
	}
	for h, got := range liveByHost {
		if _, ok := desiredByHost[h]; !ok {
			d.ToRemove = append(d.ToRemove, got)
		}
	}
	sort.Slice(d.ToAdd, func(i, j int) bool { return d.ToAdd[i].Hostname < d.ToAdd[j].Hostname })
	sort.Slice(d.ToRemove, func(i, j int) bool { return d.ToRemove[i].Hostname < d.ToRemove[j].Hostname })
	sort.Slice(d.ToChange, func(i, j int) bool { return d.ToChange[i].New.Hostname < d.ToChange[j].New.Hostname })
	return d
}
