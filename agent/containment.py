"""Process containment for the `bash` tool.

The path sandbox confines the file tools. It does nothing for the shell: a
command runs with the user's privileges and can read or write anywhere the user
can. This module narrows that where the platform allows it.

Two layers, applied together:

* **Resource limits** (POSIX, always on) — CPU time, address space, file size,
  and process count, applied with `setrlimit` in the child. These stop a runaway
  build, a fork bomb, or a test that allocates without bound from taking the
  machine down. Portable enough to be the baseline.
* **Filesystem and network containment** (Linux with `bubblewrap`) — the project
  root read-write, everything else read-only, and no network. This is what turns
  "the agent only touches your project" from a convention into a property.

`bubblewrap` rather than hand-rolled namespaces: it is packaged, needs no root,
and is small enough to audit. On Windows and macOS only the resource limits and
the output/time caps apply, which is stated plainly rather than implied.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from . import config

log = logging.getLogger(__name__)


class ContainmentUnavailable(RuntimeError):
    """`AGENT_CONTAINMENT=require` was set but containment cannot be applied."""


def bwrap_path() -> str | None:
    return shutil.which("bwrap")


def available() -> tuple[bool, str]:
    """Whether filesystem/network containment can be applied here."""
    if sys.platform != "linux":
        return False, f"bubblewrap needs Linux (platform is {sys.platform})"
    if bwrap_path() is None:
        return False, "bubblewrap not found on PATH (apt install bubblewrap)"
    try:
        with open("/proc/sys/kernel/unprivileged_userns_clone") as fh:
            if fh.read().strip() == "0":
                return False, "unprivileged user namespaces are disabled by sysctl"
    except OSError:
        pass  # absent on most kernels; absence is not a failure
    return True, "bubblewrap available"


@dataclass(frozen=True)
class Containment:
    """What is actually in force for this session."""

    confined: bool
    reason: str
    rlimits: bool

    @property
    def summary(self) -> str:
        parts = ["rlimits" if self.rlimits else "no-rlimits"]
        parts.append("confined" if self.confined else "unconfined")
        return " · ".join(parts)


def status() -> Containment:
    """Resolve the configured mode against what this platform can do."""
    mode = (config.CONTAINMENT or "auto").lower()
    ok, reason = available()
    rlimits = os.name == "posix"
    if mode == "off":
        return Containment(False, "disabled by AGENT_CONTAINMENT=off", rlimits)
    if mode == "require" and not ok:
        raise ContainmentUnavailable(
            f"AGENT_CONTAINMENT=require but containment is unavailable: {reason}"
        )
    return Containment(ok, reason, rlimits)


def wrap(argv: Sequence[str], root: str | os.PathLike[str]) -> list[str]:
    """Wrap a command so it runs confined, or return it unchanged.

    Read-write inside `root`, read-only for the system paths a build needs, no
    network, and the child dies with the agent. Anything not bound is simply not
    present in the child's filesystem view.
    """
    state = status()
    if not state.confined:
        return list(argv)

    root = Path(root).resolve()
    cmd: list[str] = [
        bwrap_path() or "bwrap",
        "--die-with-parent",
        "--unshare-pid",
        "--unshare-net",       # the local-only guarantee, enforced
        "--unshare-uts",
        "--unshare-ipc",
        "--new-session",       # no terminal to hijack
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
    ]
    for path in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/opt"):
        if Path(path).exists():
            cmd += ["--ro-bind", path, path]
    cmd += ["--bind", str(root), str(root), "--chdir", str(root), "--"]
    return cmd + list(argv)


def rlimit_preexec():
    """A `preexec_fn` applying resource limits, or None where unsupported.

    Returned as a closure so the limits are read once at call time and the child
    does nothing but `setrlimit` between fork and exec.
    """
    if os.name != "posix":
        return None
    try:
        import resource
    except ImportError:  # pragma: no cover - POSIX only
        return None

    limits: list[tuple[int, int]] = []
    if config.BASH_CPU_SECONDS > 0:
        limits.append((resource.RLIMIT_CPU, config.BASH_CPU_SECONDS))
    if config.BASH_MEMORY_MB > 0:
        limits.append((resource.RLIMIT_AS, config.BASH_MEMORY_MB * 1024 * 1024))
    if config.BASH_MAX_FILE_MB > 0:
        limits.append((resource.RLIMIT_FSIZE, config.BASH_MAX_FILE_MB * 1024 * 1024))
    if config.BASH_MAX_PROCS > 0:
        limits.append((resource.RLIMIT_NPROC, config.BASH_MAX_PROCS))
    limits.append((resource.RLIMIT_CORE, 0))  # no core dumps

    def _apply() -> None:  # pragma: no cover - runs in the forked child
        for which, value in limits:
            try:
                soft, hard = resource.getrlimit(which)
                ceiling = value if hard == resource.RLIM_INFINITY else min(value, hard)
                resource.setrlimit(which, (ceiling, hard))
            except (ValueError, OSError):
                pass  # a limit we cannot set is not a reason to fail the command

    return _apply


def scrubbed_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """A copy of the environment with credential-bearing variables removed.

    The agent reads repository files on the user's behalf; it should not also
    hand an arbitrary command whatever tokens happen to be exported in the
    parent shell.
    """
    env = dict(base if base is not None else os.environ)
    for name in config.BASH_ENV_DENY:
        env.pop(name, None)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PIP_NO_INPUT", "1")
    env.setdefault("DEBIAN_FRONTEND", "noninteractive")
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return env
