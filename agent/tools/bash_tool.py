"""bash tool (opt-in) — run a shell command in the project root and return its
stdout, stderr, and exit code, so the agent can COMPILE / LINT / TYPE-CHECK /
TEST the code and surface real errors, not just read it.

SAFETY: this executes arbitrary commands with your user's privileges. It is
DISABLED unless the agent is started with --allow-exec (or AGENT_ALLOW_EXEC=1).
There is NO container: a hostile repo's build script or test file runs as you.
Guardrails here are best-effort, not a security boundary:
  - a deny-list blocks obviously catastrophic commands,
  - a hard timeout kills long/hung runs,
  - the exact command is echoed back for transparency,
  - commands run under real `bash -c` when available (else the platform shell).
Prefer running this inside a container. Only enable it for code you trust enough
to run on this machine.
"""

import os
import re
import shutil
import subprocess

from .. import config
from .base import Tool

# Best-effort deny-list: patterns that are almost never legitimate for a
# compile/lint/test agent and would be destructive. This is a guardrail against
# the model fat-fingering something catastrophic, NOT a sandbox — string matching
# cannot make arbitrary execution safe.
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
    r"\bpip\s+(?:install|uninstall)\b",  # no dependency changes
    r"\bnpm\s+(?:install|i|ci|uninstall)\b",
]
_DENY_RE = [re.compile(p, re.IGNORECASE) for p in _DENY]

_MAX_STREAM = 20000  # chars kept per stream before the loop's own budgeting


def _tail(s):
    if not s or len(s) <= _MAX_STREAM:
        return s or ""
    return f"... [truncated {len(s) - _MAX_STREAM} chars] ...\n" + s[-_MAX_STREAM:]


def _bash(args, sandbox):
    if not config.ALLOW_EXEC:
        return (
            "ERROR: command execution is disabled. Start the agent with --allow-exec "
            "(or set AGENT_ALLOW_EXEC=1) to enable the bash tool."
        )
    command = (args.get("command") or "").strip()
    if not command:
        return "ERROR: no command given"
    for rx in _DENY_RE:
        if rx.search(command):
            return (
                f"REFUSED: command matches a blocked destructive pattern "
                f"(/{rx.pattern}/). Not run. Rephrase to a read-only check "
                f"(compile/lint/test); this tool must not install, publish, or delete."
            )

    timeout = args.get("timeout") or config.EXEC_TIMEOUT
    try:
        timeout = max(1, min(int(timeout), config.EXEC_TIMEOUT_MAX))
    except (TypeError, ValueError):
        timeout = config.EXEC_TIMEOUT

    # Prefer real bash (consistent semantics on the Windows/MINGW box too); fall
    # back to the platform shell (cmd.exe / sh) if bash isn't on PATH.
    bash = shutil.which("bash")
    if bash:
        argv, use_shell = [bash, "-c", command], False
    else:
        argv, use_shell = command, True

    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run(
            argv,
            shell=use_shell,
            cwd=str(sandbox.root),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as e:
        partial = e.stdout if isinstance(e.stdout, str) else ""
        return (
            f"$ {command}\n[timed out after {timeout}s — process killed]\n"
            + _tail(partial)
        ).rstrip()
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


bash_tool = Tool(
    name="bash",
    description=(
        "Run a shell command in the project root and return its stdout, stderr, and "
        "exit code. Use this to COMPILE, LINT, TYPE-CHECK, or TEST the code and find "
        "real errors — e.g. `python -m py_compile path/to/file.py`, `pytest -x -q`, "
        "`ruff check .`, `tsc --noEmit`, `cargo check`, `go build ./...`. A non-zero "
        "exit code means it failed: read the stderr, then use `read` on the cited "
        "file:line to explain or fix the error. Do NOT install packages, push, or use "
        "the network — run checks only."
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
)
