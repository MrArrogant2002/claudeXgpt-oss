# Build Plan — Write / Edit Tools, Claude Code–style (permission-first)

The point of this tier is **not** "add three file-writing functions." In Claude Code the
file tools are trivial; the real system is the **permission layer** that sits in front of
every mutating action. So this plan is built the same way: **the permission engine is the
architecture, the tools hang off it.** (Grounded in our own `claude-internal-structure.md`
§"The permission system" and §"Permission resolution chain".)

## 1. The permission model (the core of this tier)

Every tool call that could mutate state passes through a **resolution chain** before it runs.
Nothing writes to disk until this chain returns `allow`.

### Permission modes
A session runs in one **mode** (settable live, like Claude's Shift+Tab cycle), most → least
permissive. We adopt the subset that fits a single-user local agent:

| Mode | Behavior |
|------|----------|
| `plan` | **Read-only.** All mutations blocked. (The safe default for pure Q&A.) |
| `default` | Interactive. The user approves each mutating action (allow once / session / always / deny). |
| `acceptEdits` | File **edits** auto-approved for the session; other mutations still prompt. |
| `bypassPermissions` | No checks. Testing / trusted batch only; never the default. |
| `dontAsk` | All allowed, still logged; **no prompts** — the headless/CI mode (never auto-prompts in one-shot runs). |

Read-only tools (`read`, `glob`, `grep`, `list_dir`, `lsp`) are unaffected by mode — they never
need permission. Only `write`/`edit`/`multi_edit` (and `bash`) go through the chain.

### The resolution chain (order matters; first decision wins)
```
tool call (mutating)
  1. Rule match      — allow / ask / deny rules from settings (deny wins over allow)
  2. tool.check_permissions(args)  — tool-specific objection (e.g. path escapes sandbox → deny)
  3. Mode policy     — plan→deny · bypass/dontAsk→allow · acceptEdits→allow if file-edit · default→ask
  4. Interactive prompt (only if still "ask")  — user: allow once | allow session | always allow | deny
```
- **Deny always wins.** A `deny` rule or a tool objection is final; no prompt can override it.
- **`always allow`** promotes the decision to a session/persisted `allow` rule (so the user isn't
  re-asked for the same tool+path pattern).
- A **PreToolUse-style hook seam** (optional, later) can decide before step 1 — the extensibility
  point Claude uses for programmatic policy.

### Rules (settings-driven, path-scoped)
Rules match on **tool name + argument specifier**, e.g.:
```jsonc
{
  "permissions": {
    "defaultMode": "default",
    "deny":  ["Edit(**/.env)", "Edit(.git/**)", "Write(vendor/**)", "Edit(**/*.pem)"],
    "allow": ["Edit(src/**)", "Edit(tests/**)"],
    "ask":   ["Write(**)"]
  }
}
```
- **Deny-by-default for sensitive paths**: `.git/`, `.env`/secrets/keys, `vendor/`, `.venv/`,
  and the agent's own source unless explicitly targeted.
- Specifiers match **canonicalized** paths (expand `~`, resolve `..`) so rules can't be dodged.
- Everything still sits **inside the `Sandbox`** — permissions are policy; the sandbox is the hard
  wall. Defense in depth: even `bypassPermissions` cannot write outside the project root.

## 2. The tools (Claude Code contracts)

Three tools, each `read_only=False`, each carrying its own `check_permissions`:

### `edit` — the workhorse (exact-string replacement)
```json
{ "path": "src/x.py", "old_string": "...", "new_string": "...", "replace_all": false }
```
- `old_string` must match **exactly once** (else error asking for more surrounding context),
  unless `replace_all`.
- **Read-before-write invariant**: the file must have been `read` this session, and its content
  must be **unchanged since that read** (external-modification detection). Stale → refuse, tell the
  model to re-read. This is precisely Claude's Edit behavior and it prevents blind clobbering.
- Returns a **unified diff** of the change (surfaced to the model and, in the TUI, to the user).

### `write` — create or overwrite a whole file
```json
{ "path": "src/new.py", "content": "..." }
```
- Creating a new file: must not already exist.
- Overwriting: requires the read-before-write invariant (must have read it, unchanged since).

### `multi_edit` — several edits to one file, atomically
- A list of `{old_string, new_string, replace_all?}` applied in order, **all-or-nothing**, so the
  file never lands in a half-edited state. (Claude's MultiEdit.)

All three: **atomic write** (temp file in the same dir → `fsync` → `os.replace`) and a **prior-bytes
snapshot** to a git-ignored backup dir, so any change is undoable.

## 3. How resolution flows in *our* agent

The loop gains a **`can_use_tool(tool, args)` gate** (Claude's `canUseTool`) called before every
tool dispatch; read-only tools short-circuit to allow. The gate implements the §1 chain and gets
its "ask" answer from a **prompter** supplied by the front-end:

- **TUI (interactive):** the prompter renders the pending change — a **diff for edits**, the command
  for `bash` — and asks **allow once / allow session / always allow / deny** (via the existing
  `on_event` seam). "always" writes a rule; "session" caches for the session.
- **CLI one-shot / headless:** no interactive prompt is possible, so the **mode decides** —
  `plan` (default) blocks mutations with a clear message; `--permission-mode acceptEdits` or
  `dontAsk` allows per policy. A one-shot run never hangs waiting for a prompt.

## 4. Integration with the existing code (cautious, additive)

```
agent/permissions.py         # modes, rule matching (allow/ask/deny), the resolution chain, decision types
agent/edits.py               # atomic write, backup/undo, read-state (freshness) registry, unified-diff render
agent/tools/edit_tool.py     # edit / write / multi_edit — each with check_permissions(), read_only=False
```
- **`Tool`** gains an optional `check_permissions(args, sandbox) -> allow|ask|deny` (defaults to
  `ask` for mutating tools, `allow` for read-only) — mirrors Claude's per-tool `checkPermissions`
  and `isReadOnly`. No change to existing tools' behavior.
- **`default_registry(..., allow_edit=None)`**: registers the write tools only when editing is
  enabled; read-only default path is untouched.
- **`config.py`**: `PERMISSION_MODE` (default `plan` for safety), `PERMISSION_RULES` (from a settings
  file), `EDIT_BACKUP_DIR`, `EDIT_MAX_BYTES`.
- **`cli.py` / `tui.py`**: `--permission-mode {plan|default|acceptEdits|bypassPermissions|dontAsk}`;
  the TUI wires the interactive prompter + diff preview; Shift-key cycle to change mode live (nice-to-have).
- **`loop.run_turn`**: accepts `can_use_tool=` and calls it before each dispatch; on `deny` it feeds a
  "permission denied" result back to the model as data (never crashes) — exactly how a denied tool is
  handled today for errors.
- **`context.py`**: reuse `budget()` for large diffs.
- **`read_tool`** records the content hash so `edit`/`write` can enforce freshness.

Nothing in inference, harmony, or the read/glob/grep/lsp tools changes.

## 5. The edit → verify loop
After approved edits, the loop nudges the model to **run the check** (`bash: pytest`/`ruff`/`tsc`)
and, on failure, re-read the failing `file:line` and fix — bounded, like the existing recovery loops.
Edits are "done" only when the check is green. (Pairs with the `bash` and `lsp` tools already built.)

## 6. Milestones
| M | Deliverable |
|---|---|
| **M1** | `permissions.py`: modes, rule matching (allow/ask/deny, deny-wins, path specifiers), resolution chain, decision types + unit tests. |
| **M2** | `edits.py` (atomic write, backup/undo, read-state/freshness, diff) + `edit` tool with `check_permissions`. |
| **M3** | `write` + `multi_edit`; sensitive-path deny defaults; `read_only`/`check_permissions` on the `Tool` interface. |
| **M4** | `can_use_tool` gate in `run_turn`; **CLI `--permission-mode`** (plan default) + headless behavior. |
| **M5** | **TUI**: diff preview + allow-once/session/always/deny prompt; live mode switch. |
| **M6** | PreToolUse hook seam; verify-loop nudges; wire into eval (patch correctness / test-pass). |

## 7. Testing (fully offline on the build machine)
Permission decisions and file edits need **no model**:
- **Permission matrix**: for each mode × (allow/ask/deny rule) × (read-only vs mutating), assert the
  chain returns the right decision; `deny` beats `allow`; sensitive paths denied; `plan` blocks all
  mutations; sandbox escape denied even under `bypassPermissions`.
- **Edit semantics**: unique match; non-unique → error; missing file → error; **stale (hash changed)
  → refused**; returns a correct diff; atomic (interrupted write leaves original intact); backup
  restores prior bytes.
- **Gate behavior**: mocked-model `run_turn` where a denied `edit` comes back to the model as a
  "permission denied" result and the turn continues; an `acceptEdits` run applies without prompting.

## 8. Risks & mitigations
- **Prompt-less headless hang** → one-shot/headless never enters interactive "ask"; the mode decides.
- **Rule bypass via path tricks** → canonicalize before matching + sandbox hard wall underneath.
- **Silent clobber** → read-before-write + freshness hash + atomic write + backups.
- **Over-broad edits** → unique `old_string`, minimal-change guidance, diff surfaced for approval,
  verify-with-tests loop.
- **Self-approving agent** → mutating tools default to `ask`; `plan` is the default mode; a future
  sub-agent tier would use a `bubble`-style escalation (never approve its own writes).

## 9. Out of scope (v1)
Git operations, delete/rename/move, the `auto` (LLM-classifier) and `bubble` (sub-agent) modes,
and LSP-driven refactors. v1 = **a real permission engine + create/edit/multi-edit**, safe and
reversible.
