"""bash tool (opt-in) — run a shell command and return its output + exit code, so the
agent can COMPILE / LINT / TEST the code, install missing dependencies, and surface real
errors, not just read it.

By default commands run in ONE persistent bash session per project (see shell_session.py),
so `cd`, `export`, and an activated venv persist across calls exactly like the user's
terminal. Set AGENT_BASH_PERSISTENT=0 for a fresh `bash -c` per call.

SAFETY: this executes arbitrary commands with your user's privileges and is DISABLED unless
the agent is started with --allow-exec (or AGENT_ALLOW_EXEC=1). There is NO container. For
fully-local/air-gapped use the command is UNRESTRICTED by design; a destructive-command
deny-list is kept in the code and re-enabled with AGENT_BASH_RESTRICTED=1 (the switch a
future networked or untrusted deployment turns on). The path sandbox and the write-tier
permission engine remain the real trust boundary; prefer running the whole agent in a
container when the repo is not trusted.
"""

import os
import re
import shutil
import subprocess

from .. import config, containment
from .base import Tool
from .shell_session import ShellSession, find_venv_activate

# Destructive-command deny-list. Applied ONLY when config.BASH_RESTRICTED is set (a future
# networked/untrusted mode); kept here, not deleted, so that switch re-enables it. It was
# never a security boundary — string matching cannot make arbitrary execution safe.
_DENY = [
    r"\brm\s+-[rf]{1,2}\b.*(?:/|~|\*)",  # rm -rf on / ~ or globs
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\b(?:shutdown|reboot|halt|poweroff)\b",
    r">\s*/dev/(?:sd|nvme|disk)",  # overwrite a raw disk
    r"\bchmod\s+-R\s+0*777\s+/",
    r":\(\)\s*\{\s*:\s*\|\s*:",  # classic fork bomb :(){ :|:& };:
    r"\b(?:sudo|su)\b",
    r"\bgit\s+push\b",  # don't publish from inside the agent
    r"\b(?:curl|wget)\b[^|]*\|\s*(?:sh|bash|zsh)\b",  # pipe-to-shell installers
    r"\b(?:mv|cp)\s+.*\s+/(?:bin|etc|usr|boot|dev|sys|lib)\b",
    r"\bformat\s+[A-Za-z]:",  # windows: format C:
    r"\bdel\s+/[sqfSQF]",  # windows: del /s /q /f
]
_DENY_RE = [re.compile(p, re.IGNORECASE) for p in _DENY]

def _max_stream() -> int:
    return max(1000, config.BASH_MAX_OUTPUT)

# One persistent session per project root (created on first use).
_SESSIONS: dict[str, ShellSession] = {}


def _tail(s: str) -> str:
    """Keep the tail of a command's output, within the configured cap.

    The tail rather than the head: a failing build puts the error at the end.
    """
    cap = _max_stream()
    if not s or len(s) <= cap:
        return s or ""
    return f"... [truncated {len(s) - cap} chars] ...\n" + s[-cap:]


def interrupt_all() -> int:
    """Signal every live shell session. Called when the user cancels a turn."""
    stopped = 0
    for sess in list(_SESSIONS.values()):
        try:
            if sess.interrupt():
                stopped += 1
        except Exception:
            pass
    return stopped


def close_all() -> None:
    """Tear down every shell session (process exit)."""
    for root, sess in list(_SESSIONS.items()):
        try:
            sess.close()
        except Exception:
            pass
        _SESSIONS.pop(root, None)


def _session_for(root: str) -> ShellSession:
    sess = _SESSIONS.get(root)
    if sess is None:
        sess = ShellSession(root, find_venv_activate(root))
        _SESSIONS[root] = sess
    return sess


def _run_persistent(command: str, root: str, timeout: int) -> str:
    code, output = _session_for(root).run(command, timeout)
    body = _tail(output)
    parts = [f"$ {command}", f"[exit {code}]"]
    parts.append(body.rstrip() if body.strip() else "(no output)")
    return "\n".join(parts)


def _run_oneshot(command: str, root: str, timeout: int) -> str:
    """Fallback (AGENT_BASH_PERSISTENT=0): a fresh shell per call — loses cd/env/venv."""
    bash = shutil.which("bash")
    activate = find_venv_activate(root)
    if bash:
        prefixed = f". '{activate}' 2>/dev/null; {command}" if activate else command
        argv: list[str] | str = [bash, "-c", prefixed]
        use_shell = False
    else:
        argv, use_shell = command, True
    if not use_shell:
        argv = containment.wrap(argv, root)
    env = containment.scrubbed_env()
    kwargs: dict = {}
    preexec = containment.rlimit_preexec()
    if preexec is not None:
        kwargs["preexec_fn"] = preexec
    try:
        proc = subprocess.run(
            argv, shell=use_shell, cwd=root, capture_output=True, text=True,
            errors="replace", timeout=timeout, env=env, **kwargs,
        )
    except subprocess.TimeoutExpired as e:
        partial = e.stdout if isinstance(e.stdout, str) else ""
        return (f"$ {command}\n[timed out after {timeout}s — process killed]\n"
                + _tail(partial)).rstrip()
    except (OSError, ValueError) as e:
        return f"$ {command}\nERROR: could not run: {type(e).__name__}: {e}"
    parts = [f"$ {command}", f"[exit {proc.returncode}]"]
    out, err = _tail(proc.stdout), _tail(proc.stderr)
    if out.strip():
        parts.append("--- stdout ---\n" + out.rstrip())
    if err.strip():
        parts.append("--- stderr ---\n" + err.rstrip())
    if not out.strip() and not err.strip():
        parts.append("(no output)")
    return "\n".join(parts)


def _guard(args, sandbox):
    """Tool-level objection for `bash`.

    The permission engine only manages tools that expose `check_permissions`.
    Without this, every command resolved to allow before any rule, mode or
    protected-path check ran — so `plan` mode was not read-only and a protected
    file that could not be edited could still be overwritten by a command.
    """
    if not (args.get("command") or "").strip():
        return "deny"
    if config.BASH_RESTRICTED:
        for rx in _DENY_RE:
            if rx.search(args["command"]):
                return "deny"
    return "ask"


def _bash(args, sandbox):
    if not config.ALLOW_EXEC:
        return (
            "ERROR: command execution is disabled. Start the agent with --allow-exec "
            "(or set AGENT_ALLOW_EXEC=1) to enable the bash tool."
        )
    command = (args.get("command") or "").strip()
    if not command:
        return "ERROR: no command given"

    # Destructive-command guard — only in restricted (networked/untrusted) mode.
    if config.BASH_RESTRICTED:
        for rx in _DENY_RE:
            if rx.search(command):
                return (
                    f"REFUSED: command matches a blocked destructive pattern "
                    f"(/{rx.pattern}/) and AGENT_BASH_RESTRICTED is set. Not run."
                )

    timeout = args.get("timeout") or config.EXEC_TIMEOUT
    try:
        timeout = max(1, min(int(timeout), config.EXEC_TIMEOUT_MAX))
    except (TypeError, ValueError):
        timeout = config.EXEC_TIMEOUT

    root = str(sandbox.root)
    if config.BASH_PERSISTENT:
        try:
            return _run_persistent(command, root, timeout)
        except (OSError, FileNotFoundError):
            return _run_oneshot(command, root, timeout)  # bash-session spawn failed
    return _run_oneshot(command, root, timeout)


bash_tool = Tool(
    name="bash",
    description=(
        "Run a shell command and return its combined output and exit code. Commands run in "
        "a persistent shell with the project's virtualenv already activated, so `cd`, "
        "`export`, and installs persist across calls. Use it to COMPILE / LINT / TYPE-CHECK "
        "/ TEST the code (e.g. `pytest -x -q`, `ruff check .`, `python -m py_compile f.py`). "
        "If a run fails for a missing package, install it into the venv (e.g. `pip install "
        "<pkg>`) and retry, then tell the user what you installed. A non-zero exit code means "
        "it failed: read the stderr, then `read` the cited file:line to explain or fix it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to run (e.g. 'pytest -x -q')",
            },
            "timeout": {
                "type": "integer",
                "description": "Max seconds before the command is killed (optional)",
            },
        },
        "required": ["command"],
    },
    run=_bash,
    read_only=False,
    check_permissions=_guard,
)
