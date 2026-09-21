# Demo project guide — `demo-project/` (Nimbus)

**What it is:** `demo-project/` is a synthetic, multi-language repository ("Nimbus", a
URL shortener with click analytics) that exists **only to exercise and evaluate the
local agent**. It gives the agent a realistic, unfamiliar codebase to map with
`local init`, navigate with the funnel tools (`glob`/`grep`/`read`/`lsp`), run with
`bash`, and modify with the write tier — without touching the agent's own source.

It is committed to this repo so it travels to the GPU box on `git pull`. It is **not**
part of the agent; nothing in `agent/` imports it. Point the agent at it with
`--project ./demo-project`.

> Keep this guide (and the "seeded bug" spoiler below) out of `demo-project/` itself,
> so the fixture stays "unknown" to the agent for honest evaluation.

## Layout (what the agent should discover)

```
demo-project/
├── README.md, ARCHITECTURE.md, CONTRIBUTING.md   # in-character project docs
├── Makefile, docker-compose.yml, Dockerfile, .env.example
├── config/            app.yaml, logging.yaml            (YAML)
├── db/                schema.sql + migrations/          (SQL)
├── services/
│   ├── api/           Python  — nimbus_api/ + tests/    (the runnable core)
│   └── worker/        Go      — clicks aggregator + test
├── web/               TypeScript app + a plain-JS util with a node test
├── cli/               nimbusctl.py                       (Python CLI)
├── libs/              pyutil/ (Python), jsutil/ (JS)     (shared code)
└── scripts/           dev.sh, seed_data.sh, run_tests.sh (Bash)
```

Languages present: **Python, Go, TypeScript, JavaScript, SQL, Bash, YAML, TOML,
Dockerfile, Makefile** — enough to make `local init` produce a real map and to test
language detection for the `lsp` tool.

## The seeded bug (spoiler — for you, not the agent)

`services/api/nimbus_api/shortener.py::encode()` skips the `n == 0` case: its
`while n > 0` loop never runs for `0`, so `encode(0)` returns `""` instead of `"0"`.
The test `services/api/tests/test_shortener.py::test_encode_zero` fails because of it
(the other 10 Python tests pass). Note the JS mirror `libs/jsutil/base62.js` handles
`0` **correctly** — a nice cross-language inconsistency for the agent to notice.

**The one-line fix** is to handle zero, e.g. add `if n == 0: return ALPHABET[0]`
before the loop (or `return "0"`).

## Try this with the agent

Run these against the fixture (on the box, with the model up). Suggested flow:

```bash
# 1) build the project map
python tui.py --project ./demo-project --allow-exec --allow-edit
# then, inside the TUI:
/init
```

Questions to ask (read-only — good for testing navigation + grounding):
- "What does this project do and what are its main components?"
- "Where is the short code generated, and how does the base62 scheme work?"
- "How does a click get recorded and counted end to end?"
- "Which languages are used and where does each live?"

Run-and-fix (needs `--allow-exec`, and `--allow-edit` to apply the fix):
- "Run the test suite and tell me what fails and why."
  → expect it to run `make test` / the Python `unittest` and find `test_encode_zero`.
- "Fix the failing test." → expect a one-line edit to `shortener.py`, then a re-run
  showing all suites green. (Approve the edit in the TUI, or use
  `--permission-mode acceptEdits`.)

Cross-language reasoning:
- "The Python and JS base62 implementations disagree on one input — which, and why?"
  → `encode(0)`: Python returns `""`, JS returns `"0"`.

## Expected results (quick reference)

| Check | Expected |
|-------|----------|
| `make test` (or Python `unittest`) | 1 failure: `test_encode_zero`; everything else passes |
| Node test | `web/format: all assertions passed` (if `node` installed) |
| Go test | `ok` for `clicks` (if `go` installed) |
| After the fix | all available suites pass |

## Why this fixture is useful for evaluation

It doubles as a task set for the eval harness (see
[evaluation-and-trustworthiness.md](research/evaluation-and-trustworthiness.md)):
*locate* (where is X), *explain* (how does Y work), *edit/run-fix* (the seeded bug),
and *cross-file/cross-language* reasoning — each with an objective, checkable answer.
