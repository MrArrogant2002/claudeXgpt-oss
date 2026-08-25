# Local Code Agent — Run Guide

A Claude Code–style code agent with **gpt-oss** as the brain, running fully local.
You render the **Harmony** prompt yourself and feed raw token IDs to **llama.cpp**'s
`/completion` endpoint — no chat template, no Ollama/vLLM wrapper. The agent answers
questions about a codebase by *navigating* it with three tools (glob → grep → read),
the way Claude Code does — no embeddings, no index.

Built on one machine, run on another (the one with the model). Instructions below
are for the **model machine**.

---

## Status: M0–M4 complete (full working agent)

| File | Role | Milestone |
|------|------|-----------|
| `agent/config.py` | Settings (env-overridable) | — |
| `agent/harmony_codec.py` | Render conversation → token IDs; parse tokens → channels | M1 |
| `agent/inference.py` | llama.cpp raw `/completion` client (token IDs in/out) | M0 |
| `agent/sandbox.py` | Path sandbox (blocks escapes outside project root) | M2 |
| `agent/context.py` | Result budgeting + drop stale chain-of-thought | M4 |
| `agent/tools/` | `glob`, `grep`, `read` + registry | M2–M3 |
| `agent/loop.py` | Orchestration loop (render→infer→parse→dispatch→repeat) | M0–M4 |
| `cli.py` | Interactive / one-shot entry point | M3 |
| `m0_smoke.py` | Raw round-trip smoke test | M0 |

Verified offline on the build machine: tools execute, sandbox blocks escapes,
Harmony renders with tool schemas, budgeting truncates. The M0 smoke test already
passed on the model machine (raw token round-trip + `return_tokens` confirmed).

---

## Prerequisites (model machine)

```bash
pip install -r requirements.txt   # openai-harmony, requests
```

Also needed (not pip): a **gpt-oss GGUF** + recent **llama.cpp** (`llama-server`),
and **ripgrep** (`rg`) on PATH (grep falls back to pure-Python if it's missing).

---

## Step 1 — start llama.cpp (raw completion, no chat template)

```bash
llama-server -m /path/to/gpt-oss-20b.gguf -c 8192 --port 8081 -ngl 999
```

- **No `--jinja`** — we render Harmony ourselves and use the raw `/completion`
  endpoint; the server must not template anything.
- `-ngl 999` = all layers on GPU (lower if VRAM is tight). `-c 8192` = context size.

## Step 2 — (optional) re-run the smoke test

```bash
python m0_smoke.py
```

Already passed for you — only re-run if you change the server/port.

## Step 3 — run the agent

Point it at the repo you want to ask about with `--project`.

**One-shot:**
```bash
python cli.py --project /path/to/repo "Where is run_turn defined and what does it do?"
```

**Interactive REPL:**
```bash
python cli.py --project /path/to/repo
```

**Just the answer (hide the tool trace):**
```bash
python cli.py --project /path/to/repo "..." 2>/dev/null
```

**See the model's reasoning (debug):**
```bash
python cli.py --project /path/to/repo --show-reasoning "..."
```

### What you'll see

- The **final answer** prints to **stdout**.
- The **tool-call trace** (`[tool call] functions.grep …` / `[tool result] …`) and
  optional reasoning print to **stderr**, so you can watch it navigate:
  glob to find files → grep to locate a symbol → read the relevant lines → answer.

### Example questions to try

```bash
python cli.py --project /path/to/repo "What does the authentication flow do?"
python cli.py --project /path/to/repo "List every file that imports the database module"
python cli.py --project /path/to/repo "Explain how errors are handled in the request loop"
```

---

## CLI flags

| Flag | Default | Meaning |
|------|---------|---------|
| `--project PATH` | cwd | Repo the tools may read (sandbox root) |
| `--reasoning low\|medium\|high` | `medium` | Reasoning effort (higher = better + slower) |
| `--show-reasoning` | off | Print the analysis channel to stderr (debug) |
| `--quiet` | off | Hide the tool-call trace |

## Configuration (env vars, override without editing code)

| Var | Default | Meaning |
|-----|---------|---------|
| `AGENT_BASE_URL` | `http://localhost:8081` | llama.cpp server root (no `/v1`) |
| `AGENT_REASONING` | `medium` | `low` \| `medium` \| `high` |
| `AGENT_MAX_TOKENS` | `2048` | max tokens generated per call |
| `AGENT_TEMPERATURE` | `0.7` | sampling temperature |
| `AGENT_MAX_TURNS` | `12` | tool-loop circuit breaker |
| `AGENT_PROJECT_ROOT` | cwd | default project root (or use `--project`) |
| `AGENT_TOOL_RESULT_CAP` | `30000` | max chars per tool result |

---

## How it works (one loop)

```
your question
  → drop stale reasoning from prior turns, add your message
  → render Harmony (system + developer[tools] + history) → token IDs
  → llama.cpp /completion (raw) → output token IDs
  → parse channels:
        analysis  = private reasoning (never shown to you)
        commentary= tool call(s): glob / grep / read
        final     = the answer
  → if tool calls: run them (sandboxed, budgeted), append results, loop
  → if final: print it; drop this turn's reasoning before the next question
```

Design choices (from build-plan.md): single agent, serial tools, in-process tools
(no MCP), fully local. Tools are read-only in v1 (glob/grep/read); editing tools
are a later tier.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `cannot reach llama.cpp` | Server not running / wrong port. Start Step 1; check `AGENT_BASE_URL`. |
| `Server returned no output token IDs` | llama.cpp too old for `return_tokens` — update it. |
| Empty final answer | Raise `--reasoning medium/high` or `AGENT_MAX_TOKENS`. |
| Grep slow / misses | Install `ripgrep` (`rg`) for speed; otherwise the Python fallback runs. |
| Answer ignores the code | Make sure `--project` points at the right repo. |

## Git workflow

`.venv/`, `__pycache__/`, `models/` are gitignored. Build here → `git push` → on the
model machine `git pull` → `pip install -r requirements.txt` → run.
