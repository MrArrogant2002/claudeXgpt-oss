"""A persistent `bash` session: environment variables and an activated virtualenv persist
across commands, but every command starts at the project root — a `cd` lasts only within
the command that issued it. This keeps the shell's cwd consistent with the file tools
(list_dir/glob/grep/read are always project-root-relative) and stops the shell from
silently drifting outside the project.

One session lives per project root for the life of the agent process. Commands are run
by writing them to the shell's stdin followed by a unique end-of-command marker that
carries the exit status; output is read back up to that marker. On timeout the session is
killed and transparently re-created on the next command (state resets, correctness holds).

Stdlib only. No network. Not a security boundary — see agent/tools/bash_tool.py.
"""

from __future__ import annotations

import os
import queue
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .. import config, containment

_ACTIVATE_CANDIDATES = (
    (".venv", "bin", "activate"),       # POSIX venv
    (".venv", "Scripts", "activate"),   # Windows / MINGW venv
    ("venv", "bin", "activate"),
    ("venv", "Scripts", "activate"),
)


def find_venv_activate(root: str | os.PathLike[str]) -> str | None:
    """Return the path to a virtualenv `activate` script under `root`, or None."""
    base = Path(root)
    for parts in _ACTIVATE_CANDIDATES:
        candidate = base.joinpath(*parts)
        if candidate.is_file():
            return str(candidate)
    return None


class ShellSession:
    """One long-lived `bash` process. `run()` is serialized by a lock."""

    def __init__(self, cwd: str, venv_activate: str | None = None) -> None:
        self._cwd = str(cwd)
        self._venv = venv_activate
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._q: queue.Queue[str | None] | None = None

    # --- lifecycle ----------------------------------------------------------
    def _start(self) -> None:
        bash = shutil.which("bash")
        if not bash:
            raise FileNotFoundError("bash not found on PATH")
        # Force line-buffering so our end-of-command marker flushes promptly even though
        # stdout is a pipe (mainly a safeguard on Linux; git-bash flushes without it).
        stdbuf = shutil.which("stdbuf")
        argv = [stdbuf, "-oL", "-eL", bash] if stdbuf else [bash]
        # Confine the whole session rather than each command: the shell is
        # long-lived, so anything it spawns inherits the same view.
        argv = containment.wrap(argv, self._cwd)
        env = containment.scrubbed_env()
        kwargs: dict[str, object] = {}
        if os.name == "posix":
            kwargs["start_new_session"] = True       # own process group -> killpg works
            preexec = containment.rlimit_preexec()
            if preexec is not None:
                kwargs["preexec_fn"] = preexec       # rlimits inherited by children

        self._proc = subprocess.Popen(  # noqa: S603 - intentional shell for the agent
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,                # merge, terminal-like ordering
            cwd=self._cwd,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **kwargs,
        )
        q: queue.Queue[str | None] = queue.Queue()
        self._q = q
        threading.Thread(
            target=self._pump, args=(self._proc.stdout, q), daemon=True
        ).start()
        # Non-interactive bash reads stdin without a prompt/echo. Remember the project
        # root as bash sees it (correct on every platform), so we can return to it before
        # each command; then activate the venv once (its env persists across commands).
        self._write('NIMBUS_ROOT="$(pwd)"\n')
        if self._venv:
            self._write(f". '{self._venv}' 2>/dev/null || true\n")

    @staticmethod
    def _pump(stream, q: queue.Queue[str | None]) -> None:
        """Reader thread: forward every line of shell output onto `q`; None on EOF."""
        try:
            for line in stream:
                q.put(line)
        except (ValueError, OSError):
            pass
        finally:
            q.put(None)

    def _write(self, text: str) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise BrokenPipeError("shell session not started")
        self._proc.stdin.write(text)
        self._proc.stdin.flush()

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def interrupt(self) -> bool:
        """Stop whatever is running now. Returns True if a process was signalled.

        Without this, Ctrl-C during a long build left the REPL apparently frozen
        until the command's own timeout expired: cancellation was only checked at
        loop step boundaries, and the worker was blocked inside run().
        """
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return False
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), signal.SIGINT)
            else:
                proc.terminate()
            return True
        except (ProcessLookupError, OSError):
            return False

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        self._q = None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, OSError):
            pass

    # --- running commands ---------------------------------------------------
    def run(self, command: str, timeout: int) -> tuple[int, str]:
        """Run `command` in the session. Returns (exit_code, combined_output).
        On timeout the session is reset and exit_code is -1."""
        with self._lock:
            if not self.alive():
                self._start()
            nonce = uuid.uuid4().hex
            marker = f"__NIMBUS_DONE_{nonce}__"
            try:
                # Re-anchor to the project root, run the command, then emit the marker +
                # its exit code. cd resets each call; env/venv persist.
                self._write(
                    'cd "$NIMBUS_ROOT" 2>/dev/null\n'
                    f"{command}\n"
                    f"printf '\\n{marker}%s__\\n' \"$?\"\n"
                )
            except (BrokenPipeError, OSError):
                self.close()
                return -1, "[shell session died; retry the command]"
            return self._collect(marker, timeout)

    def _collect(self, marker: str, timeout: int) -> tuple[int, str]:
        assert self._q is not None
        deadline = time.monotonic() + timeout
        chunks: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                partial = "".join(chunks).rstrip()
                self.close()  # reset a hung shell; next run() respawns + re-activates venv
                note = f"[timed out after {timeout}s — shell reset]"
                return -1, (partial + "\n" + note if partial else note)
            try:
                line = self._q.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue
            if line is None:                       # shell exited/EOF
                self._proc = None
                return -1, "".join(chunks).rstrip()
            pos = line.find(marker)
            if pos == -1:
                chunks.append(line)
                continue
            if pos > 0:                            # text sharing the marker's line
                chunks.append(line[:pos])
            code_str = line[pos + len(marker):].split("__", 1)[0].strip()
            try:
                exit_code = int(code_str)
            except ValueError:
                exit_code = -1
            return exit_code, "".join(chunks).rstrip()
