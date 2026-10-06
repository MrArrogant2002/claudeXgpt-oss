"""The decoder facade the turn loop talks to (build plan, Phase 4 wiring).

`loop.run_turn` should not know which experimental arm is running, nor how to
construct a transport. It asks a `Decoder` for a completion and gets back the
assembled token list plus the server's final response dict, exactly as the
legacy single-request path returned. Everything that differs between arms —
how many round trips, where a grammar applied — stays behind this boundary and
is recorded to the trace.

Passing `decoder=None` to the loop keeps the original single-request behaviour,
so the arm is opt-in and the pre-existing path is untouched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from .client import CompletionClient, DecodeResult, LlamaCppClient
from .strategies import DecodingStrategy, build
from .tokens import SpecialTokens

log = logging.getLogger(__name__)


@dataclass
class Decoder:
    """A decoding arm bound to a transport, with tracing attached."""

    strategy: DecodingStrategy
    client: CompletionClient
    trace: Any = None

    @property
    def name(self) -> str:
        return self.strategy.name

    def decode(
        self,
        prefill_ids: Sequence[int],
        *,
        tools: Iterable[Any],
        max_tokens: int,
        cancel: Any = None,
        on_delta: Callable[[str, str], None] | None = None,
    ) -> DecodeResult:
        result = self.strategy.generate(
            prefill_ids,
            tools=tools,
            client=self.client,
            max_tokens=max_tokens,
            cancel=cancel,
            on_delta=on_delta,
        )
        self._record(result)
        return result

    def _record(self, result: DecodeResult) -> None:
        if self.trace is None:
            return
        try:
            for phase in result.phases:
                self.trace.decode_phase(
                    strategy=result.strategy,
                    phase=phase.phase,
                    tokens=phase.tokens,
                    constrained=phase.constrained,
                    recipient=phase.recipient,
                    latency_ms=phase.latency_ms,
                )
            self.trace.event(
                "decode",
                strategy=result.strategy,
                round_trips=result.round_trips,
                phases=result.phase_count,
                latency_ms=result.latency_ms,
                injected_tokens=result.injected_tokens,
                fallback=bool(result.raw.get("cscd_fallback")),
                recipient=result.raw.get("recipient"),
            )
        except Exception:  # instrumentation must never break a turn
            log.debug("decoder: trace write failed", exc_info=True)


def build_decoder(
    settings: Any,
    enc: Any,
    *,
    counters: Any = None,
    trace: Any = None,
    client: CompletionClient | None = None,
) -> Decoder | None:
    """Construct the decoder for `settings.decoding`, including `unconstrained`.

    The unconstrained arm is built too, rather than falling through to the
    loop's original single-request path. That path emits no decode events, so a
    baseline run produced no round-trip or latency figures and CSCD's cost could
    not be compared against anything. `UnconstrainedStrategy` issues exactly one
    unconstrained streamed request, which is what the original path did, and it
    is instrumented identically.

    Pass `decoder=None` to `run_turn` directly to get the untouched legacy path.
    """
    arm = getattr(settings, "decoding", "unconstrained")
    specials = SpecialTokens.from_encoding(enc)
    transport = client or LlamaCppClient(settings.sampling, counters=counters)
    return Decoder(
        strategy=build(arm, enc, specials), client=transport, trace=trace
    )
