# Contributing to Nimbus

## Layout
- `services/` — one directory per backend service (`api`, `worker`).
- `web/` — the browser client.
- `libs/` — code shared across services/languages.
- `db/` — schema + migrations. `config/` — runtime config. `scripts/` — dev helpers.

## Running the tests
```bash
make test            # all suites (skips go/node if not installed)
make test-api        # python:  cd services/api && python -m unittest discover -s tests
make test-web        # node:    node web/test/format.test.js
make test-worker     # go:      cd services/worker && go test ./...
```

## Style
- Keep the API dependency-free (stdlib only) so it runs anywhere with no install.
- Every behavioural change needs a test. Prefer small, pure functions.
- If you touch the short-code scheme, update **both** `services/api/nimbus_api/shortener.py`
  and its JS mirror `libs/jsutil/base62.js`, and keep `decode(encode(n)) == n`.

## Commit messages
Short imperative subject, e.g. `api: reject urls over the length limit`.
