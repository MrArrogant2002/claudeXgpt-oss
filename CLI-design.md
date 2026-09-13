# CLI Design — a Claude Code–style interactive terminal coding agent

Goal: make our **local gpt-oss agent** look and feel like the Claude Code CLI in the
reference screenshot — the pixel mascot banner, the warm-coral accent on a warm-dark ground,
a scroll-append transcript with a persistent bordered input and a mode/hint footer — while
staying **fully local** and building on the existing `tui.py` / `agent/ui/*`.

> Palette note: earlier the TUI used an amber theme (to *differ* from Claude). This design
> deliberately targets **Claude's coral** because that's the requested look. Colors live in
> one file (`agent/ui/theme.py`), so it's a one-place swap either way.

---

## 1. The reference, decomposed

```
┌ HEADER ──────────────────────────────────────────────────────────────────────┐
│  ▟▙   Claude Code  v2.1.270                                                    │
│ ▜▛▜▛  Opus 5 (1M context) with xhigh effort · API Usage Billing               │
│       C:\Users\Eswar balu                                                      │
├ NOTICE (left gutter bar) ─────────────────────────────────────────────────────┤
│ │ Using Opus 5 (1M context) (from .claude\settings.json) · /model             │
├ TRANSCRIPT (scroll-append, empty at start) ───────────────────────────────────┤
│                                                                               │
│                                                                               │
├ STATUS (right-aligned) ───────────────────────────────── Not logged in · /login┤
│ › Try "refactor <filepath>"                                                   │  ← input (ghost text)
├ FOOTER (hints/mode) ──────────────────────────────────────────────────────────┤
│ ⊞ manual mode on · ? for shortcuts · ← for agents                             │
└───────────────────────────────────────────────────────────────────────────────┘
```

Six regions, top→bottom: **header banner**, **notice line** (left `│` gutter), **transcript**,
**status line** (right-aligned), **input box** (coral `›`, ghost placeholder), **footer** (mode +
hints). The transcript scrolls; the last three stick to the bottom.

---

## 2. Design language

- **One accent, warm-dark ground, minimal chrome.** A single coral accent carries identity;
  everything else is warm neutrals. No boxes-in-boxes; whitespace does the separating.
- **Scroll-append, not full-screen.** Content prints into scrollback (like a REPL); only the
  input + footer are a live, redrawn bottom region. (This is what makes it feel like Claude Code
  and not a dashboard.)
- **Monospace, quiet motion.** A single braille spinner while thinking; a blinking coral cursor;
  no gratuitous animation.

### Palette (Claude coral on warm dark)
| token | role | hex |
|---|---|---|
| `accent` | mascot, `›` prompt, active spinner, highlights | `#D97757` (coral) |
| `accent-dim` | secondary coral (borders, selected) | `#B45309` / `#9A5B3F` |
| `fg` | primary text | `#E8E6E1` (warm off-white) |
| `dim` | metadata, hints, ghost text, notice gutter | `#8B8681` (warm gray) |
| `ground` | terminal background | `#1E1C1A` (warm near-black) |
| `panel` | input underline / subtle fills | `#2A2724` |
| `ok` | success / additions in diffs | `#7FB069` |
| `err` | errors, "Not logged in", deletions | `#E5654A` (coral-red) |
| `warn` | permission prompts, mode badges | `#E0A458` (amber) |

Neutrals are **warm** (a hair toward the coral), never pure slate — that's what reads as
"Claude" rather than "generic dark terminal."

### Glyphs
`›` prompt · `⏺` tool call · `└`/`⎿` result · `✻` thinking · `⊞` mode badge · `│` notice gutter ·
`▸/▾` collapsed/expanded · braille spinner `⠋⠙⠹…`. All have ASCII fallbacks.

---

## 3. The startup banner

```
 ▄▟█▙▄     local code agent  v0.4
▟█▀▀▀█▙    gpt-oss-20b · 32K context · fully local · edits: ask
▜█▄▄▄█▛    ~/Desktop/CODEBRAIN-RESEARCH/claudeXgpt-oss/flask
 ▀▜█▛▀
                                              llama-server ● connected · :8081
│ tokenizer offline (vendor/tiktoken) · /help for commands · /model to switch
```

- **Mascot**: a small pixel sprite drawn with half-block glyphs (`▄▀█▟▙▜▛`) in **coral** — a
  local-agent creature, not Claude's. Ship it as a constant sprite (a handful of rows); it's the
  one decorative flourish.
- **Title line**: `local code agent  vX.Y` (name bold-coral, version dim).
- **Model line**: `gpt-oss-20b · <ctx> context · fully local · edits: <mode>` — mirrors Claude's
  "model · effort · billing," but says *our* truth (local, permission mode).
- **cwd** (dim), abbreviated with `~`.
- **Right-aligned status** (Claude's "Not logged in"): our health line —
  `llama-server ● connected · :8081` (green dot) or `● unreachable` (coral-red) with a hint to
  start it. This is the honest local analogue of login/billing.
- **Notice line** with the left `│` gutter (dim): a single contextual tip, e.g. the offline
  tokenizer state, or `Using gpt-oss-20b (from AGENT_* env) · /model`.

---

## 4. The input box

- **Full-width, underlined** (a coral-dim rule under the line, like the screenshot), a bold-coral
  **`›`** prompt, then the caret.
- **Ghost placeholder** in `dim`, rotating between real, useful prompts:
  `Try "explain how Session.send works"` · `Try "where is retry logic defined?"` ·
  `Try "compile and fix the failing test"`. Clears on first keystroke.
- **Multiline**: `Enter` submits, `Shift+Enter`/`Alt+Enter` inserts a newline; pasted multi-line
  text expands the box.
- **History** (`↑/↓`, persisted), **`/`-command autocomplete** (menu above the input), bracketed
  paste. (These already exist via `prompt_toolkit` in `agent/ui/session.py`.)

---

## 5. The footer / mode line

Left side (Claude's `⊞ manual mode on · ? for shortcuts · ← for agents`), adapted:

```
⊞ ask-edits · ⚡ exec:on · ? shortcuts · ⇧⭾ cycle mode        18.4k/32k ▓▓▓▓░░ · 1.8k tok
```

- **Mode badge** = the **write-tier permission mode** (`plan` → `ask-edits` → `accept-edits`),
  cycled live with **Shift+Tab** — the Claude gesture. Color shifts: `plan` dim, `ask` amber,
  `accept` coral (louder = more powerful). This is the single most "Claude-like" interaction and
  it maps 1:1 onto the permission engine you already built.
- **exec badge** when `--allow-exec` (`⚡ exec:on`).
- `? shortcuts` opens a keybinding overlay; `⇧⭾ cycle mode` advertises the gesture.
- **Right-aligned**: the context meter + tokens (already tracked in `inference.USAGE`).

---

## 6. Transcript — turn anatomy (Claude-styled)

```
› where is retry backoff handled and can we make it jittered?

✻ Thinking…  (2.1s)                                         ⎿ esc to interrupt

⏺ lsp  defs "backoff"
  ⎿ src/urllib3/util/retry.py:412  method get_backoff_time
⏺ read  src/urllib3/util/retry.py:400-440
  ⎿ 41 lines

Backoff is computed in `Retry.get_backoff_time()` (retry.py:412): it's
`backoff_factor * 2**(consecutive_errors)`, capped at `BACKOFF_MAX`. It is
**not** jittered today…

⏺ edit  retry.py  «return min(…)» → «return min(…) + random.uniform(0, 0.1*…)»
  ┌ permission ─────────────────────────────────────────────┐
  │ edit  src/urllib3/util/retry.py                          │
  │   - return min(self.BACKOFF_MAX, backoff_value)          │
  │   + return min(self.BACKOFF_MAX, backoff_value) + jitter │
  │ allow?  [y] once   [s] session   [a] always   [N] no     │
  └─────────────────────────────────────────────────────────┘
```

- **User message**: coral `›` + text (echoed, or just the submitted line).
- **Thinking**: dim `✻ Thinking… (Ns)` with an `esc to interrupt` affordance on the right; hidden
  reasoning by default, `/show-reasoning` to expand.
- **Tool calls**: `⏺ <tool>  <args>` (coral bullet, tool name bold), result summarized under `⎿`,
  `▸ expand` for the full budgeted output.
- **Streaming answer**: types out live (P3), markdown-lite, `file:line` citations in dim.
- **Permission prompt** (the M5 approval, upgraded to Claude's boxed dialog): a bordered panel
  showing a **real unified diff** (green `+` / coral-red `-`) and the `[y]/[s]/[a]/[N]` choices.
- **Errors / denials / interrupts**: one dim/coral line (`permission denied`, `interrupted`,
  `stopped: max turns`).

---

## 7. Interaction & keybindings

| Key | Action |
|---|---|
| `Enter` | submit · `Shift/Alt+Enter` | newline |
| `Shift+Tab` | **cycle permission mode** (plan → ask → accept) |
| `Esc` | interrupt the running turn (instant, mid-stream) · `Ctrl+C` | cancel input / interrupt |
| `↑ / ↓` | input history · `Ctrl+L` | clear screen · `Ctrl+D` | quit |
| `?` | shortcuts overlay |
| `/` | command menu (autocomplete) |

**Slash commands**: `/help` · `/model` · `/reasoning low|medium|high` · `/exec on|off` ·
`/edit on|off` · `/mode plan|ask|accept` · `/show-reasoning` · `/tokens` · `/clear` · `/exit`.

---

## 8. States (bottom region redraw)

```
startup ──▶ idle ──(submit)──▶ thinking/streaming ──▶ tool-running ──┐
   ▲          ▲                                                       │
   │          │◀── rendering answer ◀── awaiting-permission ◀─────────┘
   └── quit   └────────────── idle (Esc/Ctrl-C interrupt) ────────────
```
Each state owns the footer text: idle → hints; thinking → spinner + `esc to interrupt`;
awaiting-permission → the boxed dialog (footer dimmed); error → coral one-liner.

---

## 9. Implementation mapping (on the existing stack)

Most of this exists; the design is mostly **presentation polish + two interactions**.

| Element | Status | Where |
|---|---|---|
| scroll-append transcript, spinner, tool blocks | ✅ built | `agent/ui/app.py`, `render.py` |
| streaming answer, instant Esc/Ctrl-C | ✅ (P3) | `app.py`, `loop.py` |
| history + `/`-autocomplete input | ✅ (P2) | `agent/ui/session.py` |
| per-edit permission prompt | ✅ (M5) | `app.py` |
| **banner + mascot + warm-coral palette** | ➕ add | `theme.py` (palette), a new `banner.py` |
| **bordered input + ghost placeholder + underline** | ➕ add | `session.py` (prompt_toolkit `Window`/`bottom_toolbar`) |
| **footer mode line + Shift+Tab cycle** | ➕ add | key binding → `engine.mode`; render in `bottom_toolbar` |
| **boxed permission dialog with real diff** | ⤴ upgrade | `app.py` `_prompt_user_for_permission` |
| `? shortcuts` overlay, `/model` picker | ➕ add | small handlers |

Notes:
- Keep **prompt_toolkit** for the input (it already gives the bottom toolbar, history,
  autocomplete, key bindings — including Shift+Tab). The bordered/underlined input is a
  `prompt_toolkit` style on the input line; the footer is its `bottom_toolbar`.
- Keep rendering **stdlib ANSI** (via `theme.py`) for the transcript — no new deps. (If richer
  markdown/diff coloring is wanted later, `rich` is the drop-in; not required.)
- The Shift+Tab mode cycle mutates the live `PermissionEngine.mode`, so the write tier reflects it
  immediately — the footer badge is the source of truth the user sees.
- Fully local, offline, theme-aware; the mascot + palette are the only genuinely new assets.

---

## 10. Build milestones (to reach the look)

| Phase | Deliverable |
|---|---|
| **A · Identity** | warm-coral palette in `theme.py`; `banner.py` (mascot + title/model/cwd/health + notice gutter). |
| **B · Chrome** | bordered/underlined input, rotating ghost placeholder; footer mode line; **Shift+Tab** mode cycle + `⚡exec`/context badges. |
| **C · Turn polish** | `⏺`/`⎿` tool blocks tuned to the reference; boxed permission dialog rendering a **real unified diff**; citation styling. |
| **D · Extras** | `? shortcuts` overlay; `/model` and `/mode` pickers; empty-state ghost rotation; small-terminal reflow. |

---

## 11. Fidelity checklist (vs the screenshot)

- [ ] Pixel mascot, coral, top-left of a 3-line header.
- [ ] Title `name vX.Y` + model/effort/mode line + cwd.
- [ ] Left-gutter `│` notice line with a `/command` pointer.
- [ ] Big quiet transcript area; content scroll-appends.
- [ ] Right-aligned status (our health line ↔ Claude's login).
- [ ] Coral `›` input with an underline and rotating ghost text.
- [ ] Footer: mode badge · `? for shortcuts` · a gesture hint · right-aligned meter.
- [ ] Warm-dark ground, single coral accent, warm-gray dims.
- [ ] Shift+Tab cycles the (permission) mode, visibly.
