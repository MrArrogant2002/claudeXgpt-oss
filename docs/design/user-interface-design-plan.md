# User Interface Design Plan — Local Code Agent (Claude Code–style TUI)

A terminal user interface (TUI) for the local code agent that **looks and feels like
Claude Code**: a scrolling conversation, live tool-call blocks, a "thinking" indicator,
a status bar with token/context usage, a bordered input, slash commands, and keyboard
shortcuts — all rendered in the terminal, running **100% locally** against gpt-oss via
llama.cpp. No cloud, no external APIs, no telemetry.

---

## 1. Goals & principles

1. **Faithful to Claude Code's look.** Terminal-native, scroll-append transcript, a live
   bottom region (spinner + status), coral/neutral palette, boxed tool calls, collapsible
   reasoning.
2. **Fully local / offline.** The UI adds *zero* network dependencies. It talks only to
   the local agent in-process, which talks only to the local `llama-server`. No browser,
   no web server, no third-party service, no analytics.
3. **Non-invasive.** The UI is a new front-end layer over the *existing* agent. The core
   (`agent/loop.py`, `agent/inference.py`, tools) is reused unchanged wherever possible;
   the current `cli.py` stays as the minimal/scriptable entry point.
4. **Single language, no frontend stack.** Because the agent is Python, the UI is Python
   and runs **in-process** — no IPC, no HTML/CSS/JS, no packaging a web app. This is the
   simplest path that also best honors "all local, no third party."
5. **Windows-first.** The run machine (the GPU box) is Windows/MINGW, so every choice must
   work on Windows terminals without `curses`.

### Interpretation of "no third-party support"

"Third-party" here is read as **no third-party *services*** — nothing that leaves the
machine (no cloud APIs, hosted models, telemetry, update pings, remote fonts/CDNs). It is
**not** read as "no open-source libraries," since the project already depends on
`openai-harmony` and `requests`. The libraries proposed below are **pure-Python,
permissively licensed (MIT/BSD), and run entirely offline**. For strict-minimalists, a
**zero-dependency (stdlib-only) fallback** is documented in §5 so the project can ship
with no new `pip` packages at all.

---

## 2. What "looks like Claude Code" means (elements to replicate)

| Claude Code element | Our mapping |
|---|---|
| Startup banner (project, model, context) | Header line: project path, `gpt-oss-20b`, context window, exec-mode badge |
| Scrolling conversation transcript | Append-only render of user turns + assistant answers |
| `✻ Thinking…` indicator | The Harmony **analysis** channel → dim, collapsible "thinking" block + spinner |
| `⏺ Tool(args)` call blocks | **commentary** tool calls → one block per `list_dir`/`glob`/`grep`/`read`/`bash` |
| Collapsible tool result (`└ …`) | Tool result → summarized one-liner, expandable to full output |
| Streaming answer text | **final** channel → markdown-rendered answer (token-streamed in Phase 2) |
| Bottom spinner + "esc to interrupt" | Live status region during a running turn |
| Token / context usage meter | Status bar from `inference.usage_snapshot()` (we already track this) |
| Bordered multi-line input `›` | prompt_toolkit input with history + autocomplete |
| Slash commands (`/help`, `/clear`) | Slash-command menu with autocomplete |
| Syntax-highlighted code | Rich + Pygments in answers and `read` output |
| Color theme (coral accent) | Claude-style palette (§8) |

### ASCII wireframe (target look)

```
┌─ local code agent ─────────────────────────────  gpt-oss-20b · ./flask · exec:off ─┐
│                                                                                     │
│  › how does Session.send build and send a request?                                  │
│                                                                                     │
│  ✻ Thinking… (2.3s)                                            ▸ show reasoning      │
│                                                                                     │
│  ⏺ grep  "def send"  src/                                                           │
│    └ 3 matches · sessions.py:412, adapters.py:461 …            ▸ expand              │
│  ⏺ read  src/requests/sessions.py:500-560                                           │
│    └ 61 lines                                                  ▸ expand              │
│                                                                                     │
│  `Session.send()` takes a **prepared** request and:                                 │
│    1. resolves the transport adapter via `get_adapter()`  (sessions.py:772)         │
│    2. dispatches to `adapter.send()` …                                              │
│                                                                                     │
├─────────────────────────────────────────────────────────────────────────────────────┤
│ › ▏                                                                                  │
└─ 18.4k / 32k ctx ▓▓▓▓▓░░░  · 1,840 tok this turn · ⏎ send · esc interrupt · /help ──┘
```

---

## 3. Why a TUI (and not a web GUI)

- **Fidelity.** Claude Code *is* a terminal program. A TUI is the only thing that actually
  "looks like Claude Code." A browser GUI would be a different aesthetic.
- **Locality & simplicity.** A TUI needs no web server, no browser, no JS bundle, no open
  port — it's the strongest match for "everything local, no third party." A local web UI
  is technically local too, but it adds a whole web stack for no aesthetic gain here.
- **In-process integration.** The agent is Python; a Python TUI calls `loop.run_turn`
  directly and consumes its `on_event` callback with zero glue. A web UI would need a
  server + websocket bridge.

A local web GUI (FastAPI + WebSocket + HTML/CSS) is noted as a **rejected alternative**:
fully local is possible, but it diverges from the Claude Code look and multiplies the
moving parts.

---

## 4. How it plugs into the existing agent

The agent already exposes the exact seam a UI needs:

- `loop.run_turn(user_text, history, registry, sandbox, *, on_event=…, context_tokens=…)`
  runs one turn and **calls `on_event(fields)` for every parsed message and tool result**,
  where `fields = {role, channel, recipient, content}`.
- `inference.usage_snapshot()` / `harmony_codec.salvage_count()` give live token + recovery
  stats for the status bar.
- `inference.context_size()` gives the real context window for the meter.

The TUI is essentially **a richer `on_event` renderer + an input loop**, replacing the
plain `print(..., file=sys.stderr)` in today's `cli.py`.

### Concurrency model (must not block the UI)

`run_turn` is synchronous and does blocking HTTP to `llama-server`. So:

1. The **UI thread** owns input + rendering.
2. Each user turn runs `run_turn` on a **worker thread**.
3. `on_event` (called from the worker) pushes render events onto a **thread-safe queue**;
   the UI thread drains the queue and updates the screen (prompt_toolkit: `call_from_executor`
   / `app.invalidate`; Textual: `post_message`).
4. **Cancellation (Esc):** a `threading.Event` is passed into `run_turn`; the loop checks
   it between model calls and tool calls and aborts cleanly → mirrors Claude Code's
   "esc to interrupt." *(Requires a small change to `loop.run_turn`, see §10.)*

### Event → widget mapping

| `on_event` field | Rendered as |
|---|---|
| `role == "tool"` | Tool **result** block (collapsed summary + expandable body) |
| `channel == "commentary"` and `recipient` | Tool **call** block: `⏺ <name>(<args>)` |
| `channel == "analysis"` | **Thinking** block (dim, hidden by default, toggle to show) |
| `role == "system"` | Status/recover note (dim): `[compact]`, `[recover]`, `[nudge]` |
| final answer (from `Result.answer`) | Assistant **answer**, markdown + syntax highlighted |
| `usage_snapshot()` delta | Status bar: context meter, tokens this turn, session total |

---

## 5. Technology & languages

**Language: Python 3.11+** — same process as the agent, no bridge. (No HTML/CSS/JS; no
second language. A future Rust port would swap in a Rust TUI — see §13.)

### Recommended stack: **Rich + prompt_toolkit**

| Library | Role | Why | License / offline |
|---|---|---|---|
| **Rich** | All output rendering: styled text, panels, spinners, `Live` regions, markdown, syntax highlighting, tables | The de-facto Python TUI-output lib; gives boxed tool calls, thinking spinner, colored bullets, and code highlighting almost for free | MIT · pure-Python · offline (pulls pure-Python `pygments`, `markdown-it-py`) |
| **prompt_toolkit** | The input line: multi-line editing, history, slash-command autocomplete, key bindings, bottom status toolbar | Powers IPython/pgcli; **native Windows backend, no `curses`**; clean async + threading integration for cancellation | BSD · pure-Python · offline |

This pairing matches Claude Code's *scroll-append transcript + live bottom region* model:
Rich renders each block into the scrollback as the turn progresses; prompt_toolkit owns the
input and status toolbar. Both enable Windows VT processing automatically.

### Alternatives (documented, not recommended as default)

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **MVP shortcut:** Rich + stdlib `input()` | One dependency; fastest to a working UI | No history / autocomplete / Esc-interrupt (input() blocks) | Good **Phase 1** stepping stone |
| **Textual** (async full-screen app, built on Rich) | Most "app-like"; CSS-like styling; reactive widgets | Full-screen alt-buffer feel is less like Claude Code's scrollback; heavier; async model fights sync `run_turn` | Alt if a dashboard feel is wanted |
| **Zero-dependency:** stdlib only (raw ANSI + `input()`/`msvcrt`) | No new `pip` packages at all | Painful cross-platform key handling; reimplement spinner/markdown/highlighting; poor input editing | Fallback if "no library" is strict |
| **Local web GUI** (FastAPI + WS + HTML/CSS/JS) | Familiar styling | Web stack, browser, open port; diverges from Claude Code look | Rejected |
| **curses / windows-curses** | stdlib-ish | `curses` absent on Windows (needs `windows-curses` = a dependency anyway); low-level | Rejected |

**Recommendation:** ship **Phase 1** as **Rich + `input()`** (single dep, quick win), then
add **prompt_toolkit** in **Phase 2** for the full Claude-Code input experience.

---

## 6. Visual design

### Layout regions
1. **Header** (top, static per session): `local code agent · gpt-oss-20b · <project> · exec:on/off`.
2. **Transcript** (scrollback, append-only): user turns, thinking blocks, tool blocks, answers.
3. **Live region** (bottom, transient during a turn): spinner + elapsed time + current action.
4. **Status bar** (bottom, persistent): context meter, tokens this turn, session total, key hints.
5. **Input** (bottom): bordered `›` prompt, multi-line, history, slash autocomplete.

### Color palette (Claude-style; 256-color/truecolor with graceful mono fallback)

| Token | Use | Suggested |
|---|---|---|
| `accent` | user `›` marker, active spinner, highlights | coral/orange (`#D97757`) |
| `assistant` | answer text | default foreground |
| `tool` | `⏺` tool-call bullet + name | cyan/blue |
| `thinking` | reasoning block | dim magenta, italic |
| `ok` | exit 0 / success | green |
| `err` | exit ≠ 0 / errors / REFUSED | red |
| `dim` | metadata, status bar, borders, `└` summaries | gray |

### Glyphs
`›` user prompt · `✻` thinking · `⏺` tool call · `└` result summary · `▸`/`▾` collapsed/expanded
· `⚠` exec-enabled badge · braille/dots spinner frames. (All have ASCII fallbacks for dumb terminals.)

### Tool-call rendering
- **Call:** `⏺ read  src/requests/sessions.py:500-560` (name accented, args dim).
- **Result:** one-line summary (`└ 61 lines`, `└ 3 matches`, `└ exit 1 · SyntaxError`),
  `▸ expand` to reveal the full budgeted body (syntax-highlighted for `read`, plain for `grep`/`bash`).
- **bash exit codes:** exit 0 → green `└ exit 0`; non-zero → red `└ exit 1` and auto-expand
  stderr (so errors are visible without a keystroke).

### Thinking blocks
Hidden by default (like Claude Code). A header `✻ Thinking… (Ns) ▸ show reasoning` that expands
the analysis text. Global toggle mirrors the existing `--show-reasoning`.

---

## 7. Feature set

- **Slash commands:** `/help`, `/clear` (reset history), `/reasoning low|medium|high`,
  `/exec on|off` (toggle the bash tool live), `/reasoning-show` (toggle thinking),
  `/tokens` (session usage), `/project <path>`, `/exit`. Autocompleted via prompt_toolkit.
- **Keybindings:** `Enter` send · `Alt/Shift+Enter` newline · `Esc` interrupt running turn ·
  `Ctrl+C` cancel input / `Ctrl+D` quit · `↑/↓` input history · `Ctrl+L` clear screen.
- **Context meter:** `used/window` with a bar, driven by the rendered-prompt token count and
  `context_size()`; turns amber past the compaction threshold (`COMPACT_RATIO`).
- **Exec badge:** header shows `exec:on ⚠` when `--allow-exec`; every `bash` call is visibly
  labeled (never silent).
- **Recovery visibility:** `[recover] malformed header salvaged`, `[recover] tool call leaked
  into reasoning`, `[compact] …` render as dim status notes — useful during evaluation.
- **Markdown + syntax highlighting** in answers; wrapping to terminal width; horizontal scroll
  avoided (code blocks wrap or scroll within their own region).

---

## 8. Streaming plan (Phase 2 — nice-to-have, not required)

Today `inference.complete` does one blocking POST and returns the whole completion, so the
UI updates **per model call** (each tool call / thinking block appears as it finishes — already
a progressive, Claude-Code-like feel). For true **token-by-token** streaming of the final answer:

1. Add a streaming path in `inference.py` using llama.cpp's `/completion` **SSE** (`"stream": true`);
   read token deltas as they arrive. (Still 100% local — same server.)
2. Feed deltas into `openai_harmony`'s **`StreamableParser`** to emit channel deltas
   (analysis/commentary/final) incrementally.
3. UI appends final-channel deltas live and animates the thinking block.

This is isolated behind the existing seam and can land after the MVP.

---

## 9. Required changes to existing code

Small, additive, and each independently testable:

1. **`loop.run_turn` — cancellation hook.** Accept `cancel: threading.Event | None`; check it
   between model calls / tool calls; return a `Result("cancelled", …)` when set. (Enables Esc.)
2. **`loop.run_turn` — expose per-turn prompt size** (optional) for the live context meter
   (it already computes `len(prefill_ids)`; surface it via `on_event`).
3. **`inference.py` — streaming** (Phase 2 only): a `complete_stream(...)` generator.
4. **New entry point** `tui.py` (or `python -m agent.ui`) alongside the existing `cli.py`.
   `cli.py` stays as the minimal, pipe-friendly interface (`… 2>/dev/null`).

Nothing in the tools, sandbox, harmony codec, or compaction changes.

---

## 10. File structure

```
tui.py                     # new entry point:  python tui.py --project ./repo [--allow-exec]
agent/
  ui/
    __init__.py
    app.py                 # main loop: input -> worker thread -> event queue -> render
    render.py              # Rich renderers: header, tool block, thinking block, answer, status bar
    events.py              # maps on_event(fields) -> render objects; queue plumbing
    theme.py               # palette, glyphs, ASCII fallbacks
    commands.py            # slash-command registry + autocomplete
    keys.py                # prompt_toolkit key bindings
requirements-ui.txt        # rich, prompt_toolkit  (kept separate from core requirements.txt)
```

Keeping UI deps in `requirements-ui.txt` means the headless `cli.py` still runs with only
the core deps — install the UI extras only on the machine that wants the TUI.

---

## 11. Milestones / roadmap

| Phase | Deliverable | Deps |
|---|---|---|
| **P1 — MVP** | Rich-rendered transcript, tool-call/result blocks, thinking blocks, status bar, spinner, `input()` prompt, basic slash commands. Feels like Claude Code, minus fancy input. | Rich |
| **P2 — Real input** | prompt_toolkit input: history, multi-line, slash autocomplete, Esc-interrupt (+ `run_turn` cancel hook), context meter. | + prompt_toolkit |
| **P3 — Streaming** | SSE streaming + `StreamableParser`; token-by-token answers, live thinking. | (server already supports SSE) |
| **P4 — Polish** | Collapsible expand/collapse, theme refinements, `bash` exit-code coloring + auto-expand stderr, resize handling, mono/ASCII fallback, `/exec` live toggle. | — |

---

## 12. Testing (respects the two-machine workflow)

- **On the build machine (no model):** unit-test `agent/ui/render.py` and `events.py` by feeding
  **mock `on_event` dicts** (reuse the same fixtures already used to test `loop.py`) and asserting
  the produced Rich renderables / plain-text snapshots. Snapshot-test the header, a tool block, a
  thinking block, an error (`exit 1`) block, and the status bar. No model, no network.
- **On the GPU box (model present):** end-to-end smoke — ask a question, confirm tool blocks stream
  in, answer renders, Esc interrupts, `--allow-exec` shows the badge and a `bash` block.
- Verify **Windows terminal** rendering (VT enabled, glyphs + colors, no `curses`).

---

## 13. Future Rust port (forward-compatibility note)

When the agent is ported to Rust (Stack A), the TUI equivalent is **`ratatui` + `crossterm`**
(the standard Rust TUI stack — pure-Rust, cross-platform, offline). The event→widget mapping,
layout regions, palette, and streaming design in this document carry over unchanged; only the
rendering library differs. Design the P1–P4 seams so this port is a re-implementation of the
*view*, not the agent.

---

## 14. Risks & open decisions

- **Dependency stance (needs your confirmation).** Recommended: Rich + prompt_toolkit (local,
  offline, MIT/BSD). If you want **zero** new `pip` packages, we take the stdlib-only fallback
  (§5) and accept a plainer input and more hand-rolled rendering. *This is the one decision that
  changes the build.*
- **Windows terminal quirks.** Some glyphs/truecolor vary by terminal (Windows Terminal vs older
  conhost/MINGW). Mitigation: capability detection + ASCII/mono fallback in `theme.py`.
- **Streaming complexity.** `StreamableParser` + partial-channel rendering is fiddlier than the
  per-turn model; keep it strictly Phase 3 and behind the existing seam.
- **Cancellation granularity.** Esc can only interrupt *between* a model call and the next step
  (a single in-flight completion still finishes). Acceptable, and matches how a blocking local
  server behaves; true mid-stream abort comes with Phase 3 streaming.

---

## 15. Out of scope (for now)

- Mouse support, split panes, a file-tree sidebar (Textual could add these later).
- Editing/writing files from the UI (the agent is read-only + opt-in `bash`; edit tools are a
  separate tier).
- Session persistence / transcript export (easy add later; not core to the look).
