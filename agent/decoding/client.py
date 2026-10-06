"""Completion transport for decoding strategies (build plan, Phase 4).

Strategies are written against this narrow interface rather than against
`agent.inference` directly, for two reasons:

* CSCD must be able to **abandon a generation mid-stream** when the recognizer
  fires, which means a strategy needs a token iterator it may stop consuming;
* the whole method has to be testable on a machine with no GPU, which `ReplayClient`
  makes possible by replaying canned token sequences.

`extra_body` carries `grammar` / `json_schema` through to llama.cpp. Whether a
given server build honours those alongside a token-id prompt is exactly what
`scripts/spike_cscd.py` establishes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol, Sequence


class CompletionClient(Protocol):
    """What a decoding strategy needs from a model server."""

    def complete(
        self,
        prefill_ids: Sequence[int],
        *,
        max_tokens: int,
        extra_body: dict[str, Any] | None = None,
    ) -> tuple[list[int], dict[str, Any]]:
        ...

    def stream(
        self,
        prefill_ids: Sequence[int],
        *,
        max_tokens: int,
        extra_body: dict[str, Any] | None = None,
        cancel: Any = None,
    ) -> Iterator[int]:
        ...


class LlamaCppClient:
    """Adapter over `agent.inference`, carrying sampling (including the seed).

    Imported lazily so that this module — and therefore the strategy tests — do
    not require `requests` or a reachable server.
    """

    def __init__(self, sampling: Any, *, counters: Any = None) -> None:
        self.sampling = sampling
        self.counters = counters

    def _body(self, extra_body: dict[str, Any] | None) -> dict[str, Any]:
        body = dict(self.sampling.to_request())
        if extra_body:
            body.update(extra_body)
        return body

    def complete(
        self,
        prefill_ids: Sequence[int],
        *,
        max_tokens: int,
        extra_body: dict[str, Any] | None = None,
    ) -> tuple[list[int], dict[str, Any]]:
        from .. import inference

        return inference.complete(
            list(prefill_ids),
            max_tokens=max_tokens,
            extra_body=self._body(extra_body),
            counters=self.counters,
        )

    def stream(
        self,
        prefill_ids: Sequence[int],
        *,
        max_tokens: int,
        extra_body: dict[str, Any] | None = None,
        cancel: Any = None,
    ) -> Iterator[int]:
        from .. import inference

        return inference.complete_stream(
            list(prefill_ids),
            max_tokens=max_tokens,
            extra_body=self._body(extra_body),
            cancel=cancel,
            counters=self.counters,
        )


@dataclass
class ReplayClient:
    """Deterministic client that replays prepared token sequences.

    Each call pops the next sequence from `responses`. `calls` records what each
    strategy actually asked the server for, which is how the tests assert that a
    constraint was applied to the right phase and only to that phase.
    """

    responses: list[list[int]]
    calls: list[dict[str, Any]] = field(default_factory=list)
    _index: int = 0

    def _next(self, prefill_ids: Sequence[int], max_tokens: int,
              extra_body: dict[str, Any] | None, streamed: bool) -> list[int]:
        if self._index >= len(self.responses):
            raise AssertionError(
                f"ReplayClient exhausted after {self._index} call(s); "
                "the strategy made more requests than the test prepared"
            )
        tokens = self.responses[self._index]
        self._index += 1
        self.calls.append(
            {
                "prefill": list(prefill_ids),
                "prefill_len": len(prefill_ids),
                "max_tokens": max_tokens,
                "extra_body": dict(extra_body or {}),
                "streamed": streamed,
                "returned": list(tokens),
            }
        )
        return list(tokens)

    def complete(
        self,
        prefill_ids: Sequence[int],
        *,
        max_tokens: int,
        extra_body: dict[str, Any] | None = None,
    ) -> tuple[list[int], dict[str, Any]]:
        tokens = self._next(prefill_ids, max_tokens, extra_body, streamed=False)
        return tokens, {"stop_type": "eos", "tokens_evaluated": len(prefill_ids)}

    def stream(
        self,
        prefill_ids: Sequence[int],
        *,
        max_tokens: int,
        extra_body: dict[str, Any] | None = None,
        cancel: Any = None,
    ) -> Iterator[int]:
        tokens = self._next(prefill_ids, max_tokens, extra_body, streamed=True)

        def gen() -> Iterator[int]:
            for t in tokens:
                if cancel is not None and cancel.is_set():
                    return
                yield t

        return gen()


@dataclass
class PhaseRecord:
    """One server round trip within a single logical completion."""

    phase: str
    tokens: int
    constrained: bool
    latency_ms: float
    recipient: str | None = None
    #: False for a phase the orchestrator emits itself. The injected header
    #: costs no server round trip, so counting it would overstate CSCD's cost by
    #: one request per tool call.
    request: bool = True


@dataclass
class DecodeResult:
    """What a strategy returns: the assembled completion plus how it was produced.

    `tokens` is the full canonical token sequence for the completion, including
    any region the orchestrator emitted itself, so the existing Harmony parser can
    consume it unchanged.
    """

    tokens: list[int]
    raw: dict[str, Any] = field(default_factory=dict)
    phases: list[PhaseRecord] = field(default_factory=list)
    strategy: str = "unconstrained"
    injected_tokens: int = 0

    @property
    def round_trips(self) -> int:
        """Server requests actually issued. Excludes orchestrator-emitted phases."""
        return sum(1 for p in self.phases if p.request)

    @property
    def phase_count(self) -> int:
        """All phases, including those the orchestrator emitted itself."""
        return len(self.phases)

    @property
    def latency_ms(self) -> float:
        return round(sum(p.latency_ms for p in self.phases), 3)


class _Clock:
    """Monotonic stopwatch in milliseconds."""

    def __enter__(self) -> "_Clock":
        self._t0 = time.monotonic()
        self.ms = 0.0
        return self

    def __exit__(self, *exc: object) -> None:
        self.ms = round((time.monotonic() - self._t0) * 1000, 3)
