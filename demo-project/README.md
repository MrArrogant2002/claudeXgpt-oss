# Nimbus

Nimbus is a small, self-hostable **URL shortener with click analytics**. It's a
reference application made of a few cooperating services written in different
languages, wired together around a shared SQLite store.

## Services

| Component | Path | Language | Responsibility |
|-----------|------|----------|----------------|
| API | `services/api` | Python | create short codes, resolve them, record clicks |
| Worker | `services/worker` | Go | aggregate raw click events into per-code totals |
| Web | `web` | TypeScript / JS | link form + stats panel |
| CLI | `cli/nimbusctl.py` | Python | shorten / resolve / stats from the terminal |

Shared bits live in `libs/` (`pyutil`, `jsutil`), the schema in `db/`, runtime
config in `config/`, and helper scripts in `scripts/`.

## Quick start

```bash
make test                                   # run every available test suite
make seed                                   # create a couple of demo links
python cli/nimbusctl.py shorten https://example.com
```

## How a link flows

1. `POST /shorten` stores the URL and returns a base62 **code**
   (`services/api/nimbus_api/shortener.py`).
2. `GET /<code>` records a click and 302-redirects to the original URL.
3. The worker rolls click events up into totals; `GET /api/stats/<code>` reports them.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full picture and
[`CONTRIBUTING.md`](CONTRIBUTING.md) to hack on it.
