// Package clicks aggregates raw click events into per-code totals.
package clicks

import "time"

// Event is a single recorded click on a short code.
type Event struct {
	Code string
	TS   time.Time
}

// Aggregator accumulates click counts keyed by short code.
type Aggregator struct {
	totals map[string]int
}

// NewAggregator returns an empty Aggregator ready to Add events.
func NewAggregator() *Aggregator {
	return &Aggregator{totals: make(map[string]int)}
}

// Add records one click event.
func (a *Aggregator) Add(e Event) {
	a.totals[e.Code]++
}

// Totals returns a defensive copy of the per-code click counts.
func (a *Aggregator) Totals() map[string]int {
	out := make(map[string]int, len(a.totals))
	for code, n := range a.totals {
		out[code] = n
	}
	return out
}
