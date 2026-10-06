"""Structured run trace (build plan, Phase 1).

Before this module the only record of a run was ANSI text scrolling past a
terminal, so nothing could be aggregated, compared, or attached to a paper. Every
quantity reported in the paper must derive from a file written here.

A run produces one JSON Lines file:

    {"kind": "manifest", ...}        exactly one, first line
    {"kind": "event", ...}           many
    {"kind": "summary", ...}         exactly one, last line

Writes are serialised by a lock because the agent runs its turn on a worker
thread. The writer never raises into the agent: tracing must not be able to break
a run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

log = logging.getLogger(__name__)


def _git_sha(root: str | os.PathLike[str]) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def _git_dirty(root: str | os.PathLike[str]) -> bool | None:
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(out.stdout.strip()) if out.returncode == 0 else None


def _pkg_version(name: str) -> str | None:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        return None


def file_sha256(path: str | os.PathLike[str], *, chunk: int = 1 << 20) -> str | None:
    """Hash a (possibly large) file, e.g. the GGUF under test. None if unreadable."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            while True:
                block = fh.read(chunk)
                if not block:
                    break
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


@dataclass
class RunCounters:
    """Per-run counters. Replaces the module-level globals in `inference` and
    `harmony_codec`, which were process-wide and therefore cross-contaminated
    any in-process sweep."""

    calls: int = 0
    prompt_tokens: int = 0
    prompt_tokens_evaluated: int = 0
    output_tokens: int = 0
    last_prompt_tokens: int = 0

    # Structural reliability — the RQ1 quantities.
    strict_parse_failures: int = 0
    salvage_invocations: int = 0
    schema_violations: int = 0
    leaked_call_dispatches: int = 0
    invalid_json_arguments: int = 0

    # Loop behaviour.
    tool_calls: int = 0
    turns: int = 0
    empty_finals: int = 0
    compactions: int = 0
    context_overflows: int = 0

    # Security.
    permission_denials: int = 0
    containment_blocks: int = 0

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class Trace:
    """JSON Lines run trace. Use as a context manager, or call `close()`.

    A disabled trace (``path=None``) is a working no-op, so call sites never need
    to guard on whether tracing is on.
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | None,
        *,
        run_id: str | None = None,
        counters: RunCounters | None = None,
    ) -> None:
        self.run_id = run_id or uuid.uuid4().hex
        self.counters = counters or RunCounters()
        self._lock = threading.Lock()
        self._fh: TextIO | None = None
        self._t0 = time.monotonic()
        self._closed = False
        self.path = Path(path) if path is not None else None
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._fh = self.path.open("a", encoding="utf-8")
            except OSError:
                log.warning("trace: cannot open %s; continuing untraced", self.path)
                self._fh = None

    # --- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "Trace":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self, **summary: Any) -> None:
        if self._closed:
            return
        self._closed = True
        self._write(
            {
                "kind": "summary",
                "counters": self.counters.to_dict(),
                "wall_seconds": round(time.monotonic() - self._t0, 3),
                **summary,
            }
        )
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

    # --- records -----------------------------------------------------------
    def manifest(self, settings: Any, **extra: Any) -> None:
        """Write the run manifest. Call once, before any event.

        `settings` is an `AgentSettings` (or anything with `to_dict`). Everything
        needed to reproduce the run belongs here: without it a trace file records
        numbers whose provenance cannot be established.
        """
        root = getattr(settings, "project_root", ".")
        record: dict[str, Any] = {
            "kind": "manifest",
            "settings": settings.to_dict() if hasattr(settings, "to_dict") else dict(settings),
            "git_sha": _git_sha(root),
            "git_dirty": _git_dirty(root),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "packages": {
                "openai-harmony": _pkg_version("openai-harmony"),
                "requests": _pkg_version("requests"),
            },
            "started_at": time.time(),
            **extra,
        }
        self._write(record)

    def event(self, kind: str, **fields: Any) -> None:
        """Record one event. `kind` is a short slug, e.g. "tool_call",
        "decode_phase", "parse", "permission"."""
        self._write({"kind": "event", "event": kind, **fields})

    def decode_phase(
        self,
        *,
        strategy: str,
        phase: str,
        tokens: int,
        constrained: bool,
        recipient: str | None = None,
        latency_ms: float | None = None,
    ) -> None:
        """A single phase of a (possibly multi-phase) decode. These records are
        what the CSCD latency-overhead and suppression numbers are computed from."""
        self.event(
            "decode_phase",
            strategy=strategy,
            phase=phase,
            tokens=tokens,
            constrained=constrained,
            recipient=recipient,
            latency_ms=latency_ms,
        )

    def parse_outcome(
        self, *, strict_ok: bool, salvaged: bool, channels: list[str]
    ) -> None:
        if not strict_ok:
            self.counters.strict_parse_failures += 1
        if salvaged:
            self.counters.salvage_invocations += 1
        self.event(
            "parse", strict_ok=strict_ok, salvaged=salvaged, channels=channels
        )

    def tool_call(
        self,
        *,
        name: str,
        args: dict[str, Any],
        result: str,
        source: str = "header",
        decision: str = "allow",
        latency_ms: float | None = None,
    ) -> None:
        """One dispatched tool call.

        `source` records *where the invocation was derived from* — "header" for a
        well-formed call, "salvage" for one reconstructed by the tolerant parser,
        "prose" for one recovered from the reasoning channel. That field is the
        dispatch-surface measurement; do not drop it.
        """
        self.counters.tool_calls += 1
        if source == "prose":
            self.counters.leaked_call_dispatches += 1
        if decision == "deny":
            self.counters.permission_denials += 1
        self.event(
            "tool_call",
            name=name,
            source=source,
            decision=decision,
            args_sha256=text_sha256(json.dumps(args, sort_keys=True, default=str)),
            args_keys=sorted(args),
            result_sha256=text_sha256(result),
            result_chars=len(result),
            latency_ms=latency_ms,
        )

    # --- internals ---------------------------------------------------------
    def _write(self, record: dict[str, Any]) -> None:
        record.setdefault("run_id", self.run_id)
        record.setdefault("t", round(time.monotonic() - self._t0, 4))
        if self._fh is None:
            return
        line = json.dumps(record, default=str, ensure_ascii=False)
        with self._lock:
            try:
                self._fh.write(line + "\n")
                self._fh.flush()
            except (OSError, ValueError):
                # Never let instrumentation break a run.
                log.debug("trace: write failed", exc_info=True)


def read_trace(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Load a trace file. Malformed lines are skipped rather than raising, so a
    truncated run (killed mid-write) is still analysable."""
    records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records
