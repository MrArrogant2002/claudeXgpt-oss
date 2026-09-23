# Local Code Agent — Run Guide

A Claude Code–style code agent with **gpt-oss** as the brain, running fully local.
You render the **Harmony** prompt yourself and feed raw token IDs to **llama.cpp**'s
`/completion` endpoint — no chat template, no Ollama/vLLM wrapper. The agent answers
questions about a codebase by *navigating* it with a tool funnel (list_dir → glob →
grep → read), the way Claude Code does — no embeddings, no index.

Built on one machine, run on another (the one with the model). Instructions below
are for the **model machine**.

> **Documentation:** design notes, research, and build plans live in [`docs/`](docs/)
> — see [`docs/README.md`](docs/README.md) for the index; architecture diagrams are in
> [`docs/architecture/`](docs/architecture/). The paper lives in
> [`research-paper/`](research-paper/).

---

## Components

| Path | Role |
|------|------|
| `agent/config.py` | Settings (env-overridable) |
| `agent/harmony_codec.py` | Render conversation → token IDs; parse tokens → channels; offline vocab |
| `agent/inference.py` | llama.cpp raw `/completion` client (token IDs in/out, streaming) |
| `agent/sandbox.py` | Path sandbox (blocks escapes outside the project root) |
| `agent/permissions.py`, `agent/edits.py` | Write-tier permission engine + atomic edits/backups |
| `agent/context.py`, `agent/compact.py` | Result budgeting; drop stale reasoning; compaction |
| `agent/project_mind.py` | `local_mind.md` project map (build/inject/staleness) |
| `agent/tools/` | `list_dir`, `glob`, `grep`, `read`, `bash`, `edit`/`write`/`multi_edit` + registry |
| `agent/loop.py` | Orchestration loop (render→infer→parse→dispatch→recover→repeat) |
| `agent/ui/` + `tui.py` | Interactive Claude Code–style TUI (the entry point) |

---

## Prerequisites (model machine)

```bash
pip install -r requirements.txt        # openai-harmony, requests
pip install -r requirements-ui.txt     # prompt_toolkit (optional, richer TUI input)
```

Also needed (not pip): a **gpt-oss GGUF** + recent **llama.cpp** (`llama-server`),
and **ripgrep** (`rg`) on PATH (grep falls back to pure-Python if it's missing).

**One-time (for fully-offline tokenizing):** download the ~3.6 MB Harmony BPE vocab
into `vendor/tiktoken/` once — see [`vendor/tiktoken/README.md`](vendor/tiktoken/README.md):

```bash
curl -L -o vendor/tiktoken/o200k_base.tiktoken \
  https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken
```

After this the agent tokenizes locally and never needs the internet.

> A ready-made Ubuntu/CUDA setup script is in [`scripts/setup.sh`](scripts/setup.sh)
> (system deps + venv + vocab, with `--llama` / `--model` to also build llama.cpp and
> fetch the model).

---

## Step 1 — start llama.cpp (raw completion, no chat template)

```bash
llama-server -m /path/to/gpt-oss-20b.gguf --port 8081 -ngl 999 \
  -c 65536 -fa on -ctk q8_0 -ctv q8_0
```

- **No `--jinja`** — we render Harmony ourselves and use the raw `/completion`
  endpoint; the server must not template anything.
- `-ngl 999` = all layers on GPU (lower if VRAM is tight).
- **Keep the native MXFP4 weights — don't re-quantize.** gpt-oss-20b is
  quantization-aware trained in MXFP4 (~13 GB) and is near-lossless as shipped;
  re-quantizing gains little and usually *costs* tool-call accuracy. The real
  levers on a ≤16 GB GPU are the KV cache and context length.
- **`-c 65536`** = context window. A code agent burns context fast; 64k is a
  comfortable target, 32k the practical minimum. gpt-oss supports up to `-c 131072`
  if you have the memory (add `--n-cpu-moe 4` to offload experts to CPU RAM).
- **`-fa on -ctk q8_0 -ctv q8_0`** = flash attention + **8-bit KV cache** — the
  single biggest memory lever on a small GPU. On a large GPU (e.g. 24–48 GB) you can
  drop the `q8_0` flags and keep **f16 KV**. (Avoid 4-bit KV: it degrades long-context
  recall and tool-argument accuracy.)

Sampling is set by the agent, not the server: it sends `temperature` **and** `top_p`
together (see the env-var table). gpt-oss's recommendation is `temperature=1.0,
top_p=1.0`; the agent defaults a bit lower for steadier tool-call formatting, with the
malformed-header salvage as a backstop. A/B `AGENT_TEMPERATURE=1.0` on the box.

## Step 2 — (recommended) build the project map — `local init`

Once per repo, have the agent read the codebase and write **`local_mind.md`** — the
offline equivalent of a `CLAUDE.md`: a concise, grounded orientation doc (overview,
build/test commands, architecture, key modules, entry points, conventions, gotchas).
In the TUI, run **`/init`** any time to build or rebuild it.

Every later query auto-loads `local_mind.md` as project context, so the agent starts
oriented instead of re-deriving the layout each turn (toggle with `AGENT_LOCAL_MIND=0`).
A fingerprint of the source tree is saved alongside it (`.local_mind.sig.json`,
git-ignored); when the code changes **massively** (many files added/removed/resized, or a
git HEAD move with edits), the TUI flags `local_mind.md` as stale at startup and suggests
`/init`. Set `AGENT_MIND_AUTO_REFRESH=1` to regenerate automatically instead.

> `/init` needs the model server running (it's a full analysis pass). Thresholds:
> `AGENT_MIND_STALE_FILES` (default 8) and `AGENT_MIND_STALE_RATIO` (default 0.15).

## Step 3 — run the agent (TUI)

Point it at the repo you want to ask about with `--project`, then type your questions:

```bash
python tui.py --project /path/to/repo
```

The answer **streams token-by-token** as the model generates it (`--no-stream` to
disable; it auto-falls-back if your llama.cpp build lacks streaming). With
`prompt_toolkit` installed you get input **history** (↑/↓) and **`/`-command
autocomplete**. Slash commands: `/help`, `/init`, `/reasoning low|medium|high`,
`/show-reasoning`, `/exec on|off`, `/mode plan|ask|accept`, `/model`, `/tokens`,
`/shortcuts`, `/clear`, `/exit`. **`Ctrl-C` interrupts a running turn instantly**
(aborts generation mid-stream). Add `--allow-exec` for the `bash` tool and
`--allow-edit` for the write tools.

### What you'll see

A live **tool-call trace** — `list_dir`/`glob` to find files → `grep` to locate a
symbol → `read` the relevant lines → the streamed **answer** — plus a status bar
(tokens, context use, permission mode). Add `--show-reasoning` to also show the
model's thinking.

### Example questions to try (type them at the prompt)

- "What does the authentication flow do?"
- "List every file that imports the database module."
- "Explain how errors are handled in the request loop."

---

## Command-line flags (`tui.py`)

| Flag | Default | Meaning |
|------|---------|---------|
| `--project PATH` | cwd | Repo the tools may read (sandbox root) |
| `--reasoning low\|medium\|high` | `medium` | Reasoning effort (higher = better + slower) |
| `--show-reasoning` | off | Show the analysis channel (the model's thinking) |
| `--quiet` | off | Hide the tool-call trace |
| `--no-stream` | off | Disable token-by-token streaming |
| `--allow-exec` | off | Enable the `bash` tool (runs shell commands to compile/lint/test). **Off by default**; only enable for code you trust to run on this machine. |
| `--allow-edit` | off | Enable the write tools (`edit`/`write`/`multi_edit`). **Off by default.** Gated by the permission engine. |
| `--permission-mode` | `plan` | Write-tier policy: `plan` (read-only) · `default` (ask) · `acceptEdits` · `bypassPermissions` · `dontAsk`. |

## Configuration (env vars, override without editing code)

| Var | Default | Meaning |
|-----|---------|---------|
| `AGENT_BASE_URL` | `http://localhost:8081` | llama.cpp server root (no `/v1`) |
| `AGENT_REASONING` | `medium` | `low` \| `medium` \| `high` |
| `AGENT_MAX_TOKENS` | `4096` | max tokens generated per call |
| `AGENT_MAX_TOKENS_CAP` | `8192`* | ceiling the recovery may escalate `n_predict` to (*≥ `AGENT_MAX_TOKENS`) |
| `AGENT_TEMPERATURE` | `0.6` | sampling temperature; gpt-oss recommends `1.0`, agent defaults lower for steadier tool calls |
| `AGENT_TOP_P` | `1.0` | nucleus sampling — sent alongside temperature |
| `AGENT_TOP_K` | `0` | top-k cutoff; `0` = disabled (only sent when > 0) |
| `AGENT_MIN_P` | `0.0` | min-p cutoff; `0` = disabled (only sent when > 0) |
| `AGENT_REPEAT_PENALTY` | `1.0` | repetition penalty; `1.0` = none (only sent when ≠ 1.0) |
| `AGENT_MAX_TURNS` | `25` | tool-loop circuit breaker |
| `AGENT_ALLOW_EXEC` | off | `1`/`true` enables the `bash` tool without `--allow-exec` |
| `AGENT_EXEC_TIMEOUT` | `60` | default seconds before a `bash` command is killed |
| `AGENT_EXEC_TIMEOUT_MAX` | `300` | hard cap the model's per-command `timeout` can't exceed |
| `AGENT_ALLOW_EDIT` | off | `1`/`true` enables the write tools without `--allow-edit` |
| `AGENT_PERMISSION_MODE` | `plan` | default write-tier permission mode (see `--permission-mode`) |
| `AGENT_EDIT_MAX_BYTES` | `2000000` | refuse writes larger than this |
| `AGENT_EDIT_BACKUP_DIR` | `.agent-backups` | per-project dir where prior file bytes are backed up (git-ignored) |
| `AGENT_LOCAL_MIND` | on | set `0` to disable auto-injecting `local_mind.md` |
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
  → drop stale reasoning from prior turns, add your message (+ local_mind.md context)
  → (if the prompt is near the context window: summarize older turns — "compaction")
  → render Harmony (system + developer[tools] + history) → token IDs
  → llama.cpp /completion (raw) → output token IDs
  → parse channels:
        analysis  = private reasoning (never shown to you)
        commentary= tool call(s): list_dir / glob / grep / read / bash / edit …
        final     = the answer
  → if tool calls: run them (sandboxed, budgeted), append results, loop
  → recover from malformed/empty output (salvage / leaked-call / tool-less synthesis)
  → if final: show it; drop this turn's reasoning before the next question
```

Design choices (from [docs/design/build-plan.md](docs/design/build-plan.md)): single
agent, serial in-process tools (no MCP), fully local. The navigation tools are
**read-only** (`list_dir`, `glob`, `grep`, `read`); execution and editing are opt-in tiers.

### Running code to find errors (`--allow-exec`)

By default the agent can only *read* code. `--allow-exec` (or `/exec on` in the TUI)
adds a **`bash`** tool so it can **compile / lint / type-check / test** the project and
find real errors — the model runs a command, reads the stderr, and `read`s the cited
`file:line` to explain or fix it.

⚠ **This runs arbitrary shell commands with your user's privileges, with no
container.** A repo you don't know can carry a hostile `conftest.py`, `Makefile`, or
build script that runs as you. Guardrails: it's **off unless you enable it**, a
deny-list blocks catastrophic commands (`rm -rf`, `sudo`, `git push`, `pip install`,
fork bombs, disk writes…), every command is echoed, and each run has a hard timeout
(`AGENT_EXEC_TIMEOUT`, capped by `AGENT_EXEC_TIMEOUT_MAX`). These are guardrails,
**not a sandbox** — only enable it for code you trust, and prefer running the whole
agent inside a container.

### Changing code — the write tier (`--allow-edit`)

By default the agent is **read-only**. `--allow-edit` adds three write tools —
**`edit`** (replace an exact, unique string), **`write`** (create/overwrite), and
**`multi_edit`** (several edits to one file, atomically) — and every mutation passes
through a **permission engine** (`agent/permissions.py`) before it touches disk.

**Permissions are the point.** A session runs in a **mode** (`--permission-mode` or
`/mode`, cycle with `Shift+Tab`), most→least safe: `plan` (read-only, the default) →
`default` (ask per edit) → `acceptEdits` → `bypassPermissions` / `dontAsk`. The
resolution chain is: **path-scoped allow/ask/deny rules (deny wins) → the tool's own
check → mode policy → prompt**, with the **`Sandbox` as the hard wall underneath** (no
write can escape the project root, ever). Secrets and VCS/vendored paths (`.env`,
`.git/`, keys, `vendor/`, `.venv/`, …) are **always denied — even under
`bypassPermissions`**.

Every edit is **read-before-write** (you must `read` a file, unchanged since, before
editing it), **atomic** (temp-file → `os.replace`), **reversible** (prior bytes saved
to `.agent-backups/`), and returns a **unified diff**. In the TUI you approve each edit
interactively (the real diff is shown); a denied edit comes back to the model as data,
never a crash. Pair `--allow-edit` with `--allow-exec` so the agent can edit → test →
fix.

**Long sessions:** the agent auto-detects the server's context window (via `/props`)
and, when a prompt approaches it, summarizes older turns into a compact note (keeping
the recent ones) so multi-question sessions don't overflow (`[compact] …` on the
trace). Reactive context-overflow recovery is the backstop.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `cannot reach llama.cpp` | Server not running / wrong port. Start Step 1; check `AGENT_BASE_URL`. |
| `Server returned no output token IDs` | llama.cpp too old for `return_tokens` — update it. |
| `Harmony tokenizer vocab unavailable` | Download the BPE vocab into `vendor/tiktoken/` once (the error prints the URL + path). If it says **corrupted**, re-download as binary — don't let an editor/Git rewrite line endings. |
| Empty final answer | The agent auto-recovers (nudges/escalates/tool-less synthesis). If it still gives up (`[no answer]`), raise `--reasoning high` or `AGENT_MAX_TOKENS`. |
| `Context window exceeded` / 400 | Raise the server context (`llama-server -c 65536` or higher). Also lower `AGENT_TOOL_RESULT_CAP`. The agent retries once by dropping reasoning. |
| Grep slow / misses | Install `ripgrep` (`rg`) for speed; otherwise the Python fallback runs. |
| Answer ignores the code | Make sure `--project` points at the right repo. |

## Git workflow

`.venv/`, `__pycache__/`, `models/` are gitignored. Build here → `git push` → on the
model machine `git pull` → `pip install -r requirements.txt` → run.
