package clicks

import "testing"

func TestAggregatorCounts(t *testing.T) {
	a := NewAggregator()
	a.Add(Event{Code: "1"})
	a.Add(Event{Code: "1"})
	a.Add(Event{Code: "2"})

	got := a.Totals()
	if got["1"] != 2 {
		t.Fatalf("code 1: want 2, got %d", got["1"])
	}
	if got["2"] != 1 {
		t.Fatalf("code 2: want 1, got %d", got["2"])
	}
}

func TestTotalsIsCopy(t *testing.T) {
	a := NewAggregator()
	a.Add(Event{Code: "1"})
	snapshot := a.Totals()
	a.Add(Event{Code: "1"})
	if snapshot["1"] != 1 {
		t.Fatalf("Totals() must return a copy; snapshot mutated to %d", snapshot["1"])
	}
}
