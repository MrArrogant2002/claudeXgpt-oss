# Nimbus Worker (Go)

Background worker that rolls raw click events up into per-code totals.

```bash
go run .          # run one demo aggregation pass
go test ./...     # run the unit tests
```

- `clicks/aggregator.go` — the `Aggregator` type (add events, read totals).
- `main.go` — a demo entry point that aggregates a fixed batch and prints totals.

In production the worker would poll the API for new click rows and flush
aggregates on `worker.flush_interval_seconds` (see `../../config/app.yaml`).
