# bash-upgrade.md — persistent shell + environment-aware, unrestricted local execution

Design + implementation plan for three linked `bash`-tool upgrades. Nothing here is built
yet; this is the "how", to review before we touch code.

> There is **no project-map / `local_mind.md` feature** — the agent navigates the repo
> live per query with the funnel tools (list_dir → glob → grep → read). Nothing is persisted.

## Goals
1. **Same-shell `bash`** — the tool runs in **one persistent bash session** that behaves
   like the user's terminal (env, `cd`, and an activated **venv** persist across calls),
   auto-activating the project venv on start.
2. **Environment awareness** — when a run fails for a missing dependency, the agent
   detects it, installs it into the venv, and retries; it tells the user what it installed.
3. **Unrestricted local `bash`** — drop the deny-list for fully-local use, but **keep the
   code behind a flag** so a future networked/untrusted mode re-enables it.

---

## Feature A — Same-shell persistent `bash` + venv auto-activation

Today each call is a fresh `bash -c "<cmd>"` (`agent/tools/bash_tool.py`), so `cd`,
`export`, and `source venv/activate` do **not** persist — the opposite of a terminal.

**A1 — persistent session (recommended).** A new `agent/tools/shell_session.py`:
- Spawn one long-lived `bash` (`bash` from PATH; on the box that is the same bash the user
  uses) with merged stdout/stderr, `cwd = project root`, inherited env +
  `PYTHONIOENCODING=utf-8`, non-interactive installers (`PIP_NO_INPUT=1`,
  `DEBIAN_FRONTEND=noninteractive`).
- On start, **auto-activate the venv** if present: source `./.venv/bin/activate` (Linux) or
  `./.venv/Scripts/activate` (Windows/MINGW). The tool then runs "inside `(.venv)`" exactly
  like the pasted terminal example.
- Run one command by writing `<cmd>\n printf "\n__DONE_<nonce>__%s\n" "$?"\n` and reading
  until the unique sentinel; the trailing token carries the exit code. State (cd/env)
  persists to the next call.
- **Timeout**: if the sentinel is not seen in `timeout` s, send `Ctrl-C` (SIGINT) to the
  child; if still stuck, kill and respawn the session. Return the partial output.
- One session per agent process (`App`), recreated on death. The loop runs tools serially,
  so no locking is needed beyond a guard.

```python
class ShellSession:
    def __init__(self, cwd: str, venv: str | None) -> None: ...
    def run(self, command: str, timeout: int) -> tuple[int, str]:  # (exit_code, output)
        ...
    def close(self) -> None: ...
```

**A2 — per-call activation (fallback).** If a persistent session proves flaky on MINGW,
run `bash -lc "source <venv-activate> 2>/dev/null; <cmd>"` each call. Keeps the venv but
loses `cd`/`export` across calls. Ship A1; keep A2 behind a config flag as a safety net.

**venv detection** (small helper in `config`/`edits`): first of `./.venv/bin/activate`,
`./.venv/Scripts/activate`, `./venv/...`; `None` if absent.

---

## Feature B — Environment / dependency awareness

Emergent once A + C land — no persisted map needed:
- When `bash` returns `ModuleNotFoundError` / `command not found` / a failed build, the
  model surfaces the missing dependency and — with the venv active and installs allowed —
  runs `pip install <pkg>` (or the project's package manager), then retries.
- One line in `EXEC_INSTRUCTIONS`: *"if a run fails for a missing package, install it into
  the venv and retry; tell the user what you installed."*
- Whether install is **auto** or **asks first** is a permission-engine decision (see C):
  default local = auto + reported; a future networked mode = ask.

No new module; this is a prompt line plus the capabilities from A/C.

---

## Feature C — Unrestricted local `bash` (deny-list kept, gated)

The professional form of "comment it out": **keep `_DENY` in the code**, but only apply it
when a flag says so — so nothing is lost and a future networked/untrusted mode flips it on.

- Add `config.BASH_RESTRICTED` (env `AGENT_BASH_RESTRICTED`, **default `False`** for local).
- In `bash_tool._bash`: `if config.BASH_RESTRICTED: <run the _DENY loop>` — otherwise skip
  it. Installs, `rm` (backups still exist via the write tier / git), and arbitrary commands
  are allowed locally.
- Update the tool description: drop "do NOT install / push / network"; say it may install
  into the venv and run project commands. (Network still only works if the box has it.)
- Keep the timeout and the command echo (transparency) unconditionally.

**Security framing (this is the paper's trust boundary).** The sandbox still confines
*file paths*; the permission engine still gates *edits*. `bash` becomes fully capable
locally by design — acceptable because the whole system is offline and air-gapped. The
`BASH_RESTRICTED` flag is the single switch a networked deployment turns on. Document this
explicitly; it is a trustworthiness result, not an afterthought.

---

## Implementation order (small, verifiable steps)

1. `config.py`: add `BASH_RESTRICTED=False`, `BASH_PERSISTENT=True`, a venv-path helper.
2. `agent/tools/shell_session.py`: the `ShellSession` (A1) + a per-call fallback (A2).
3. `agent/tools/bash_tool.py`: use the session; gate `_DENY` behind `BASH_RESTRICTED`;
   refresh the description.
4. `agent/permissions.py`: allow installs locally (gate on the same flag for a future mode).
5. `agent/loop.py`: one line in `EXEC_INSTRUCTIONS` (install-on-missing, then retry).

**Offline verification** (two-machine rule): unit-test `ShellSession` with harmless
commands (`echo`, `pwd`, `export X=1; echo $X` across two calls to prove persistence),
venv detection on a fixture, and the `BASH_RESTRICTED` gate (deny on, allow off). Live runs
happen only on the GPU box.

## Open decisions (confirm before building)
- **Auto-install: silent or ask-once?** Default proposed: install locally + report; ask in
  a future networked mode.
- **Persistent vs per-call default?** Proposed: persistent (A1) on, A2 as a fallback flag.
