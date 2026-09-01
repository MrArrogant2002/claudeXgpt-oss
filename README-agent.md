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
llama-server -m /path/to/gpt-oss-20b.gguf -c 32768 --port 8081 -ngl 999
```

- **No `--jinja`** — we render Harmony ourselves and use the raw `/completion`
  endpoint; the server must not template anything.
- `-ngl 999` = all layers on GPU (lower if VRAM is tight).
- **`-c 32768`** = context window. Bigger is better for a code agent (reading files
  + reasoning adds up fast); `-c 8192` overflows quickly on real repos. gpt-oss
  supports up to 131072 (`-c 131072`) if you have the memory.

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

**Claude Code–style TUI** (interactive, colored, live tool trace + status bar):
```bash
python tui.py --project /path/to/repo
```
Stdlib-only (no extra `pip` installs). Slash commands: `/help`, `/reasoning
low|medium|high`, `/show-reasoning`, `/exec on|off`, `/tokens`, `/clear`, `/exit`.
`Ctrl-C` interrupts a running turn; `--allow-exec` enables the `bash` tool. Use
`cli.py` (above) when you want to pipe just the answer; `tui.py` for interactive use.

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

## Step 4 — full-loop test (recommended)

M0 proved the round-trip; this proves the whole funnel (glob→grep→read→answer)
against a bundled fixture repo with a known answer:

```bash
python e2e_test.py
```

It asserts the agent (1) completed, (2) actually called ≥1 tool, and (3) grounded
its answer in the code (mentions `auth.py` / `validate_token` / the token check).
Exit code 0 = all checks passed. If some checks fail, bump `--reasoning` or tweak
the developer instructions in `agent/loop.py`.

---

## CLI flags

| Flag | Default | Meaning |
|------|---------|---------|
| `--project PATH` | cwd | Repo the tools may read (sandbox root) |
| `--reasoning low\|medium\|high` | `medium` | Reasoning effort (higher = better + slower) |
| `--show-reasoning` | off | Print the analysis channel to stderr (debug) |
| `--quiet` | off | Hide the tool-call trace |
| `--allow-exec` | off | Enable the `bash` tool (runs shell commands to compile/lint/test). **Off by default**; only enable for code you trust to run on this machine. |

## Configuration (env vars, override without editing code)

| Var | Default | Meaning |
|-----|---------|---------|
| `AGENT_BASE_URL` | `http://localhost:8081` | llama.cpp server root (no `/v1`) |
| `AGENT_REASONING` | `medium` | `low` \| `medium` \| `high` |
| `AGENT_MAX_TOKENS` | `4096` | max tokens generated per call |
| `AGENT_TEMPERATURE` | `0.3` | sampling temperature (low = more reliable tool calls + reproducible; raise for warmer prose) |
| `AGENT_MAX_TURNS` | `12` | tool-loop circuit breaker |
| `AGENT_ALLOW_EXEC` | off | `1`/`true` enables the `bash` tool without `--allow-exec` |
| `AGENT_EXEC_TIMEOUT` | `60` | default seconds before a `bash` command is killed |
| `AGENT_EXEC_TIMEOUT_MAX` | `300` | hard cap the model's per-command `timeout` can't exceed |
| `AGENT_PROJECT_ROOT` | cwd | default project root (or use `--project`) |
| `AGENT_TOOL_RESULT_CAP` | `12000` | max chars per tool result |
| `AGENT_READ_DEFAULT_LINES` | `300` | lines `read` returns when no end line is given |
| `AGENT_CONTEXT_TOKENS` | `32768` | fallback context window if `/props` auto-detect fails |
| `AGENT_COMPACT_RATIO` | `0.75` | summarize older turns once the prompt passes this fraction of the window |
| `AGENT_COMPACT_KEEP_RECENT` | `6` | recent messages kept verbatim when compacting |

---

## How it works (one loop)

```
your question
  → drop stale reasoning from prior turns, add your message
  → (if the prompt is near the context window: summarize older turns — "compaction")
  → render Harmony (system + developer[tools] + history) → token IDs
  → llama.cpp /completion (raw) → output token IDs
  → parse channels:
        analysis  = private reasoning (never shown to you)
        commentary= tool call(s): list_dir / glob / grep / read
        final     = the answer
  → if tool calls: run them (sandboxed, budgeted), append results, loop
  → if final: print it; drop this turn's reasoning before the next question
```

Design choices (from build-plan.md): single agent, serial tools, in-process tools
(no MCP), fully local. The navigation tools are **read-only** (`list_dir`, `glob`,
`grep`, `read`); editing tools are a later tier.

### Running code to find errors (`--allow-exec`)

By default the agent can only *read* code. Pass `--allow-exec` to add a **`bash`**
tool so it can actually **compile / lint / type-check / test** the project and find
real errors — the model runs a command, reads the stderr, and `read`s the cited
`file:line` to explain or fix it.

```bash
python cli.py --project /path/to/repo --allow-exec "compile the project and tell me what's broken"
```

⚠ **This runs arbitrary shell commands with your user's privileges, with no
container.** A repo you don't know can carry a hostile `conftest.py`, `Makefile`, or
build script that runs as you. Guardrails: it's **off unless you pass the flag**, a
deny-list blocks catastrophic commands (`rm -rf`, `sudo`, `git push`, `pip install`,
fork bombs, disk writes…), every command is echoed to stderr, and each run has a hard
timeout (`AGENT_EXEC_TIMEOUT`, capped by `AGENT_EXEC_TIMEOUT_MAX`). These are
guardrails, **not a sandbox** — only enable it for code you trust, and prefer running
the whole agent inside a container.

**Long sessions (M5):** the agent auto-detects the server's context window (via
`/props`) and, when a prompt approaches it, summarizes older turns into a compact
note (keeping the recent ones) so multi-question sessions don't overflow. You'll see
a `[compact] summarized older turns …` line on stderr when it fires. The reactive
context-overflow recovery is still the backstop.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `cannot reach llama.cpp` | Server not running / wrong port. Start Step 1; check `AGENT_BASE_URL`. |
| `Server returned no output token IDs` | llama.cpp too old for `return_tokens` — update it. |
| Empty final answer | The agent now auto-recovers (nudges/escalates). If it still gives up (`[no answer]`), raise `--reasoning high` or `AGENT_MAX_TOKENS`. |
| `Context window exceeded` / 400 | Raise the server context: `llama-server -c 32768` (or higher). Also lower `AGENT_TOOL_RESULT_CAP`. The agent retries once by dropping reasoning. |
| Grep slow / misses | Install `ripgrep` (`rg`) for speed; otherwise the Python fallback runs. |
| Answer ignores the code | Make sure `--project` points at the right repo. |

## Git workflow

`.venv/`, `__pycache__/`, `models/` are gitignored. Build here → `git push` → on the
model machine `git pull` → `pip install -r requirements.txt` → run.
