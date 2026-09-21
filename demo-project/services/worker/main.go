// Command worker consumes click events and periodically flushes aggregated
// per-code totals back to the Nimbus API. In this demo it runs a single
// in-memory pass over a fixed batch and prints the totals.
package main

import (
	"fmt"
	"time"

	"github.com/nimbus/worker/clicks"
)

func main() {
	agg := clicks.NewAggregator()
	batch := []clicks.Event{
		{Code: "1", TS: time.Now()},
		{Code: "1", TS: time.Now()},
		{Code: "27", TS: time.Now()},
	}
	for _, e := range batch {
		agg.Add(e)
	}
	for code, n := range agg.Totals() {
		fmt.Printf("code=%s clicks=%d\n", code, n)
	}
}
