# Nimbus architecture

Nimbus is a shared-database system: the services don't call each other directly,
they cooperate through one SQLite database (`db/schema.sql`).

```
        ┌────────────┐   POST /shorten     ┌──────────────────┐
 user → │  web (TS)  │ ───────────────────▶│   API (Python)   │
        │ link form  │   GET /<code>       │  app.py router   │
        └────────────┘◀─── 302 redirect ───│  store.py (sqlite)│
                                            └────────┬─────────┘
                                                     │ writes clicks
                                                     ▼
                                            ┌──────────────────┐
                                            │  worker (Go)     │
                                            │  clicks.Aggregator│
                                            └──────────────────┘
```

## Components

- **API (`services/api/nimbus_api`)**
  - `app.py` — a framework-free router: `handle(method, path, body) -> (status, payload)`.
  - `shortener.py` — base62 `encode`/`decode`; a link's integer id ⇄ its short code.
  - `store.py` — `LinkStore`, SQLite persistence for links and clicks.
  - `validators.py` — http(s) URL validation + host normalization.
  - `config.py` — env-driven settings (alphabet, DB path, limits).
- **Worker (`services/worker`)** — `clicks.Aggregator` counts click events per code.
- **Web (`web/src`)** — `ApiClient` + `LinkForm` + `StatsPanel`; formatting helpers in
  `util/format.js` (plain JS so it's testable without a build).
- **Shared (`libs/`)** — `pyutil/hashing.py`; `jsutil/base62.js` is the JS mirror of the
  Python shortener so the web/CLI can compute codes locally.

## The short-code scheme

Ids auto-increment from 1 in the `links` table and are base62-encoded (alphabet in
`config.ALPHABET`) into the short code. `decode(encode(n)) == n` must hold for every
non-negative id. The JS mirror (`libs/jsutil/base62.js`) must stay behaviourally
identical to the Python one.

## Data model

See `db/schema.sql` (and the incremental `db/migrations/`):
- `links(id, url, created)`
- `clicks(link_id, ts)` with an index on `link_id` for aggregation.

## Conventions

- Python: stdlib only, `unittest` tests under each service's `tests/`.
- Go: standard `go test ./...`.
- Web: TypeScript for app code; plain JS + node `assert` for unit-testable utilities.
- Config comes from env vars with local-friendly defaults (`.env.example`).
