"""Immutable per-run settings (build plan, Phase 1).

`config.py` holds module-level globals that the TUI mutates at runtime
(`config.ALLOW_EXEC = True`, `/exec on`). That makes two differently-configured
agents in one process impossible, which in turn makes an in-process experiment
sweep impossible — and it leaves nothing to serialise into a run manifest.

`AgentSettings` is the replacement: frozen, explicit, and serialisable. New code
takes a settings object; `from_config()` snapshots the legacy module so a manifest
records what actually ran during the migration period.

Nothing here reads the environment at import time.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal

ReasoningEffort = Literal["low", "medium", "high"]
DecodingArm = Literal["unconstrained", "global_schema", "cscd_i", "cscd_g"]
ContainmentProfile = Literal["none", "denylist", "enforced"]
PermissionMode = Literal[
    "plan", "default", "acceptEdits", "bypassPermissions", "dontAsk"
]


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("", "0", "false", "no", "off")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class SamplingSettings:
    """Generation parameters. `seed` is the field that makes a run reproducible;
    llama.cpp defaults to a random seed when none is sent, which is why every
    result produced before this existed is unreproducible."""

    temperature: float = 0.6
    top_p: float = 1.0
    top_k: int = 0
    min_p: float = 0.0
    repeat_penalty: float = 1.0
    seed: int | None = None
    max_tokens: int = 4096
    max_tokens_cap: int = 8192

    @classmethod
    def greedy(cls, seed: int = 0) -> "SamplingSettings":
        """Deterministic arm: the strictest reproducibility setting available
        client-side. Numerical non-determinism in batched inference still
        requires a single server slot."""
        return cls(temperature=0.0, top_p=1.0, top_k=1, seed=seed)

    def to_request(self) -> dict[str, Any]:
        """Parameters for a /completion body. Neutral values are omitted so a
        default run does not override the server's own configuration."""
        params: dict[str, Any] = {
            "temperature": self.temperature,
            "top_p": self.top_p,
        }
        if self.top_k > 0:
            params["top_k"] = self.top_k
        if self.min_p > 0:
            params["min_p"] = self.min_p
        if self.repeat_penalty != 1.0:
            params["repeat_penalty"] = self.repeat_penalty
        if self.seed is not None:
            params["seed"] = self.seed
        return params


@dataclass(frozen=True)
class AgentSettings:
    """One immutable configuration. Serialise into the run manifest verbatim."""

    project_root: str = "."
    base_url: str = "http://localhost:8081"

    reasoning: ReasoningEffort = "medium"
    sampling: SamplingSettings = field(default_factory=SamplingSettings)

    # Experimental arms -----------------------------------------------------
    decoding: DecodingArm = "unconstrained"
    containment: ContainmentProfile = "none"
    # Recovery policies enabled this run. An ablation arm is this set, not a
    # code variant. "leaked_call" is the mechanism studied in the paper.
    recovery: frozenset[str] = frozenset({"salvage", "nudge", "escalate", "synthesis"})

    # Capability tiers ------------------------------------------------------
    allow_exec: bool = False
    allow_edit: bool = False
    permission_mode: PermissionMode = "plan"

    # Budgets ---------------------------------------------------------------
    max_turns: int = 25
    context_tokens: int = 32768
    tool_result_cap: int = 12000
    read_default_lines: int = 300
    compact_ratio: float = 0.75
    compact_keep_recent: int = 6

    # Tool behaviour --------------------------------------------------------
    # Which grep backend to use. "auto" is what the legacy code did and is the
    # reason results differed between machines: ripgrep honours .gitignore and
    # skips hidden files, the Python fallback does neither. Pin it for any
    # measured run.
    grep_backend: Literal["auto", "ripgrep", "python"] = "auto"
    read_max_bytes: int = 8_000_000

    # Instrumentation -------------------------------------------------------
    trace_path: str | None = None
    run_id: str | None = None

    @classmethod
    def from_env(cls) -> "AgentSettings":
        sampling = SamplingSettings(
            temperature=_env_float("AGENT_TEMPERATURE", 0.6),
            top_p=_env_float("AGENT_TOP_P", 1.0),
            top_k=_env_int("AGENT_TOP_K", 0),
            min_p=_env_float("AGENT_MIN_P", 0.0),
            repeat_penalty=_env_float("AGENT_REPEAT_PENALTY", 1.0),
            seed=(
                _env_int("AGENT_SEED", 0)
                if os.environ.get("AGENT_SEED") is not None
                else None
            ),
            max_tokens=_env_int("AGENT_MAX_TOKENS", 4096),
            max_tokens_cap=_env_int("AGENT_MAX_TOKENS_CAP", 8192),
        )
        recovery_raw = os.environ.get("AGENT_RECOVERY")
        recovery = (
            frozenset(p.strip() for p in recovery_raw.split(",") if p.strip())
            if recovery_raw is not None
            else cls.recovery
        )
        return cls(
            project_root=os.environ.get("AGENT_PROJECT_ROOT", os.getcwd()),
            base_url=os.environ.get("AGENT_BASE_URL", "http://localhost:8081").rstrip("/"),
            reasoning=os.environ.get("AGENT_REASONING", "medium"),  # type: ignore[arg-type]
            sampling=sampling,
            decoding=os.environ.get("AGENT_DECODING", "unconstrained"),  # type: ignore[arg-type]
            containment=os.environ.get("AGENT_CONTAINMENT", "none"),  # type: ignore[arg-type]
            recovery=recovery,
            allow_exec=_env_flag("AGENT_ALLOW_EXEC"),
            allow_edit=_env_flag("AGENT_ALLOW_EDIT"),
            permission_mode=os.environ.get("AGENT_PERMISSION_MODE", "plan"),  # type: ignore[arg-type]
            max_turns=_env_int("AGENT_MAX_TURNS", 25),
            context_tokens=_env_int("AGENT_CONTEXT_TOKENS", 32768),
            tool_result_cap=_env_int("AGENT_TOOL_RESULT_CAP", 12000),
            read_default_lines=_env_int("AGENT_READ_DEFAULT_LINES", 300),
            compact_ratio=_env_float("AGENT_COMPACT_RATIO", 0.75),
            compact_keep_recent=_env_int("AGENT_COMPACT_KEEP_RECENT", 6),
            grep_backend=os.environ.get("AGENT_GREP_BACKEND", "auto"),  # type: ignore[arg-type]
            read_max_bytes=_env_int("AGENT_READ_MAX_BYTES", 8_000_000),
            trace_path=os.environ.get("AGENT_TRACE_PATH"),
        )

    @classmethod
    def from_config(cls, config_module: Any) -> "AgentSettings":
        """Snapshot the legacy `agent.config` module.

        Used while legacy call sites still read module globals, so a run manifest
        records the configuration that actually ran rather than the one the
        environment would have produced.
        """
        sampling = SamplingSettings(
            temperature=getattr(config_module, "TEMPERATURE", 0.6),
            top_p=getattr(config_module, "TOP_P", 1.0),
            top_k=getattr(config_module, "TOP_K", 0),
            min_p=getattr(config_module, "MIN_P", 0.0),
            repeat_penalty=getattr(config_module, "REPEAT_PENALTY", 1.0),
            seed=getattr(config_module, "SEED", None),
            max_tokens=getattr(config_module, "MAX_TOKENS", 4096),
            max_tokens_cap=getattr(config_module, "MAX_TOKENS_CAP", 8192),
        )
        return cls(
            project_root=str(getattr(config_module, "PROJECT_ROOT", ".")),
            base_url=getattr(config_module, "BASE_URL", "http://localhost:8081"),
            reasoning=getattr(config_module, "REASONING_EFFORT", "medium"),
            sampling=sampling,
            allow_exec=bool(getattr(config_module, "ALLOW_EXEC", False)),
            allow_edit=bool(getattr(config_module, "ALLOW_EDIT", False)),
            permission_mode=getattr(config_module, "PERMISSION_MODE", "plan"),
            max_turns=getattr(config_module, "MAX_TURNS", 25),
            context_tokens=getattr(config_module, "CONTEXT_TOKENS", 32768),
            tool_result_cap=getattr(config_module, "TOOL_RESULT_CAP", 12000),
            read_default_lines=getattr(config_module, "READ_DEFAULT_LINES", 300),
            compact_ratio=getattr(config_module, "COMPACT_RATIO", 0.75),
            compact_keep_recent=getattr(config_module, "COMPACT_KEEP_RECENT", 6),
        )

    def with_(self, **changes: Any) -> "AgentSettings":
        """Return a copy with fields replaced — how an experiment sweep builds arms."""
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["recovery"] = sorted(self.recovery)
        return d
