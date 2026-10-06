"""Capability profiles — enforcement below the agent process (build plan, Phase 6).

The permission engine is *policy*: it decides whether a mutating tool call is
allowed. It has never been *enforcement*, and it never covered the execution
tier at all — a tool with no `check_permissions` resolves to allow before any
rule is consulted, and `bash` is such a tool. With execution enabled, every
property the engine documents (secret paths denied in every mode, no write
outside the project root) can be sidestepped by running a command.

This module closes that by compiling a permission mode into a capability profile
and enforcing it with unprivileged user namespaces via `bubblewrap`:

* read-only bind mounts outside the project root,
* read-write only within it,
* `--unshare-net`, which is what turns "air-gapped" from a claim about the
  deployment environment into a property of the process,
* termination with the parent.

`bubblewrap` rather than hand-rolled `unshare`/`seccomp`: it is packaged, needs
no root, and is small enough to show in a figure. A hand-rolled equivalent is
weeks of work and invites subtle, unreviewable errors.

The `none` profile reproduces today's unconfined behaviour. It is retained
deliberately: it is the control arm of the security experiment, not dead code.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Sequence

log = logging.getLogger(__name__)

ProfileName = Literal["none", "denylist", "enforced"]


class ContainmentUnavailable(RuntimeError):
    """The requested profile cannot be enforced on this platform or build.

    Raised rather than silently degrading: an experiment arm labelled "enforced"
    that quietly ran unconfined would invalidate the security results.
    """


@dataclass(frozen=True)
class CapabilityProfile:
    """What a tool invocation is permitted to touch."""

    name: ProfileName
    project_root: Path
    writable: tuple[Path, ...] = ()
    readable: tuple[Path, ...] = ()
    network: bool = True
    enforced: bool = False
    #: Paths that must be unreachable even for reading. Used by the injection
    #: suite's canary, and by secret material the permission engine already
    #: denies writes to but has never denied reads of.
    blocked: tuple[Path, ...] = ()

    @property
    def description(self) -> str:
        bits = [
            f"write={'ro' if not self.writable else ','.join(str(p) for p in self.writable)}",
            f"net={'on' if self.network else 'off'}",
            f"enforced={'yes' if self.enforced else 'no'}",
        ]
        return " ".join(bits)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "project_root": str(self.project_root),
            "writable": [str(p) for p in self.writable],
            "readable": [str(p) for p in self.readable],
            "network": self.network,
            "enforced": self.enforced,
            "blocked": [str(p) for p in self.blocked],
        }


def profile_for(
    name: ProfileName,
    project_root: str | os.PathLike[str],
    *,
    blocked: Sequence[str | os.PathLike[str]] = (),
) -> CapabilityProfile:
    """Compile a profile name into a capability profile."""
    root = Path(project_root).resolve()
    blocked_paths = tuple(Path(b).resolve() for b in blocked)

    if name == "none":
        return CapabilityProfile(
            name="none", project_root=root, writable=(root,), network=True,
            enforced=False, blocked=blocked_paths,
        )
    if name == "denylist":
        # Pattern-matching on command text. Kept as an experimental arm because
        # it is what the agent shipped with, and because the paper should show
        # what it is worth — string matching cannot constrain arbitrary execution.
        return CapabilityProfile(
            name="denylist", project_root=root, writable=(root,), network=True,
            enforced=False, blocked=blocked_paths,
        )
    if name == "enforced":
        return CapabilityProfile(
            name="enforced", project_root=root, writable=(root,),
            readable=(Path("/usr"), Path("/bin"), Path("/lib"), Path("/lib64"),
                      Path("/etc"), Path("/opt")),
            network=False, enforced=True, blocked=blocked_paths,
        )
    raise ValueError(f"unknown containment profile {name!r}")


def bwrap_path() -> str | None:
    return shutil.which("bwrap")


def available(profile: CapabilityProfile) -> tuple[bool, str]:
    """Whether `profile` can actually be enforced here. Returns (ok, reason)."""
    if not profile.enforced:
        return True, "profile requires no enforcement"
    if sys.platform != "linux":
        return False, f"user namespaces are Linux-only (platform={sys.platform})"
    if bwrap_path() is None:
        return False, "bubblewrap (bwrap) not found on PATH — apt install bubblewrap"
    try:
        with open("/proc/sys/kernel/unprivileged_userns_clone") as fh:
            if fh.read().strip() == "0":
                return False, "unprivileged user namespaces are disabled by sysctl"
    except OSError:
        pass  # absent on many kernels; absence is not a failure
    return True, "bubblewrap available"


def wrap_argv(argv: Sequence[str], profile: CapabilityProfile) -> list[str]:
    """Return `argv` wrapped so it executes under `profile`.

    An unenforced profile returns the command unchanged. An enforced profile on a
    platform that cannot enforce it raises rather than running unconfined.
    """
    if not profile.enforced:
        return list(argv)

    ok, reason = available(profile)
    if not ok:
        raise ContainmentUnavailable(
            f"containment profile {profile.name!r} cannot be enforced: {reason}"
        )

    cmd: list[str] = [bwrap_path() or "bwrap", "--die-with-parent", "--unshare-pid"]

    if not profile.network:
        cmd.append("--unshare-net")

    for path in profile.readable:
        if path.exists():
            cmd += ["--ro-bind", str(path), str(path)]

    cmd += ["--bind", str(profile.project_root), str(profile.project_root)]
    for path in profile.writable:
        if path != profile.project_root and path.exists():
            cmd += ["--bind", str(path), str(path)]

    cmd += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]

    # Hide blocked paths outright rather than relying on a read-only mount: the
    # injection suite's canary must be *absent*, not merely unwritable.
    for path in profile.blocked:
        cmd += ["--tmpfs", str(path)] if path.is_dir() else ["--bind-try", "/dev/null", str(path)]

    cmd += ["--chdir", str(profile.project_root), "--"]
    return cmd + list(argv)


@dataclass
class ContainmentReport:
    """What actually happened, for the run manifest and the security tables."""

    profile: str
    enforced: bool
    reason: str
    blocks: int = 0
    details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def describe(profile: CapabilityProfile) -> ContainmentReport:
    ok, reason = available(profile)
    return ContainmentReport(
        profile=profile.name,
        enforced=profile.enforced and ok,
        reason=reason,
    )
