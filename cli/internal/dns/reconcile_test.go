package dns

import (
	"reflect"
	"testing"
)

func TestComputeDesired(t *testing.T) {
	blocks := []Block{
		{Hostname: "jellyfin.lan", HasHTTP: true, Managed: true, Upstream: "192.168.3.8:8096"},
		{Hostname: "infra-bin.lan", HasHTTP: true, Managed: false},
		{Hostname: "nvr.lan", HasHTTP: true, Managed: true},
		{Hostname: "nvr.lan", HasHTTPS: true, Managed: true},
	}
	extras := []ExtraEntry{{Name: "mc-vanilla.lan", IP: "192.168.3.14"}}
	got := ComputeDesired(blocks, extras, "192.168.3.12")
	want := []Record{
		{"infra-bin.lan", "192.168.3.12"},
		{"jellyfin.lan", "192.168.3.12"},
		{"mc-vanilla.lan", "192.168.3.14"},
		{"nvr.lan", "192.168.3.12"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %+v\nwant %+v", got, want)
	}
}

func TestComputeDrift_AddRemoveChange(t *testing.T) {
	desired := []Record{
		{"keep.lan", "192.168.3.12"},
		{"add.lan", "192.168.3.12"},
		{"changed.lan", "192.168.3.12"},
	}
	live := []Record{
		{"keep.lan", "192.168.3.12"},
		{"remove.lan", "192.168.3.12"},
		{"changed.lan", "192.168.3.99"},
	}
	d := ComputeDrift(desired, live)
	if len(d.ToAdd) != 1 || d.ToAdd[0].Hostname != "add.lan" {
		t.Errorf("ToAdd = %+v", d.ToAdd)
	}
	if len(d.ToRemove) != 1 || d.ToRemove[0].Hostname != "remove.lan" {
		t.Errorf("ToRemove = %+v", d.ToRemove)
	}
	if len(d.ToChange) != 1 || d.ToChange[0].New.IP != "192.168.3.12" || d.ToChange[0].Old.IP != "192.168.3.99" {
		t.Errorf("ToChange = %+v", d.ToChange)
	}
}

func TestComputeDrift_Clean(t *testing.T) {
	r := []Record{{"x.lan", "1.2.3.4"}}
	d := ComputeDrift(r, r)
	if len(d.ToAdd)+len(d.ToRemove)+len(d.ToChange) != 0 {
		t.Errorf("expected no drift, got %+v", d)
	}
}
