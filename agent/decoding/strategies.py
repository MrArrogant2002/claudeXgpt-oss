"""Decoding strategies — the experimental arms (build plan, Phase 4).

Each strategy answers one question: *where does a grammar apply during a
completion?* They are interchangeable, so an experimental arm is a configuration
value rather than a code variant.

    unconstrained   nowhere (baseline)
    global_schema   over the whole completion (reproduces the reported costs)
    cscd_i          over the recipient and the argument body; the surrounding
                    header is emitted by the orchestrator
    cscd_g          over the whole header tail and the argument body

CSCD rests on one property of the Harmony format: a completion separates
free-form reasoning from a rigid header and a rigid argument body, and the
recognizer can detect the boundary *while tokens are still arriving*. Phase A is
therefore never constrained, which is what distinguishes this from whole-
completion constraint and why neither a reasoning-quality cost nor tool-call
suppression can arise by construction.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable, Sequence

from .client import CompletionClient, DecodeResult, PhaseRecord, _Clock
from .gbnf import schema_for_tool, tool_name_grammar, union_schema
from .recognizer import Event, HarmonyRecognizer
from .tokens import SpecialTokens

log = logging.getLogger(__name__)

#: Phase B only has to produce a tool name; a tight cap keeps a server that
#: ignores the grammar from running away.
_NAME_BUDGET = 16


class DecodingStrategy:
    """Base class. Subclasses implement `generate`."""

    name: str = "unconstrained"

    def __init__(self, enc: Any, specials: SpecialTokens | None = None) -> None:
        self.enc = enc
        self.specials = specials or SpecialTokens.from_encoding(enc)

    # --- helpers shared by subclasses -------------------------------------
    def _encode(self, text: str) -> list[int]:
        return self.enc.encode(text, allowed_special="all")

    def _recognizer(self) -> HarmonyRecognizer:
        return HarmonyRecognizer(enc=self.enc, specials=self.specials)

    def _strip_specials(self, ids: Sequence[int]) -> list[int]:
        """Drop special tokens from a constrained generation.

        llama.cpp emits an end-of-generation token once a grammar or JSON schema
        is satisfied, so a Phase B response arrives as `read<|call|>` and a
        Phase C response as `{...}<|call|>`. Filtering at the token-id level
        rather than by regex on decoded text avoids mangling a body that
        legitimately contains angle brackets.
        """
        out: list[int] = []
        for t in ids:
            try:
                if self.enc.is_special_token(t):
                    continue
            except Exception:
                pass
            out.append(t)
        return out

    def _collect(
        self,
        client: CompletionClient,
        prefill: Sequence[int],
        *,
        max_tokens: int,
        extra_body: dict[str, Any] | None,
        cancel: Any,
        on_delta: Callable[[str, str], None] | None,
        stop_on_commentary: bool,
        recognizer: HarmonyRecognizer | None = None,
    ) -> tuple[list[int], bool]:
        """Stream a phase, optionally halting at a commentary onset.

        Returns (tokens, halted_at_commentary).
        """
        rec = recognizer if recognizer is not None else self._recognizer()
        out: list[int] = []
        halted = False
        raw: dict[str, Any] = {}
        gen = client.stream(
            prefill, max_tokens=max_tokens, extra_body=extra_body, cancel=cancel
        )
        # Driven by hand rather than with `for`, because the generator's RETURN
        # value carries the server's final response dict, and the turn loop needs
        # it to tell a natural stop from a truncation at n_predict.
        while True:
            try:
                tok = next(gen)
            except StopIteration as stop:
                raw = stop.value or {}
                break
            if cancel is not None and cancel.is_set():
                break
            out.append(tok)
            event = rec.push(tok)
            if on_delta is not None and rec.channel:
                # Display only; the authoritative parse happens on the full
                # assembled token list.
                try:
                    on_delta(rec.channel, self.enc.decode([tok]))
                except Exception:
                    pass
            if stop_on_commentary and event is Event.COMMENTARY_ONSET:
                halted = True
                break
        close = getattr(gen, "close", None)
        if close is not None:
            # Abandoning the iterator closes the HTTP response, which stops
            # generation server-side rather than letting it run to completion.
            try:
                close()
            except Exception:
                pass
        return out, halted, raw

    def generate(
        self,
        prefill_ids: Sequence[int],
        *,
        tools: Iterable[Any],
        client: CompletionClient,
        max_tokens: int,
        cancel: Any = None,
        on_delta: Callable[[str, str], None] | None = None,
    ) -> DecodeResult:
        raise NotImplementedError


class UnconstrainedStrategy(DecodingStrategy):
    """Baseline: one request, no grammar. What the agent did before this work."""

    name = "unconstrained"

    def generate(
        self,
        prefill_ids: Sequence[int],
        *,
        tools: Iterable[Any],
        client: CompletionClient,
        max_tokens: int,
        cancel: Any = None,
        on_delta: Callable[[str, str], None] | None = None,
    ) -> DecodeResult:
        with _Clock() as clock:
            tokens, _, raw = self._collect(
                client,
                prefill_ids,
                max_tokens=max_tokens,
                extra_body=None,
                cancel=cancel,
                on_delta=on_delta,
                stop_on_commentary=False,
            )
        return DecodeResult(
            tokens=tokens,
            raw=raw,
            strategy=self.name,
            phases=[
                PhaseRecord("full", len(tokens), constrained=False, latency_ms=clock.ms)
            ],
        )


class GlobalSchemaStrategy(DecodingStrategy):
    """Whole-completion schema constraint — the arm that reproduces the reported
    costs. The union schema must admit every registered tool's parameters, so it
    is strictly weaker than a per-recipient schema, and applying it across the
    reasoning channel is what the literature attributes the constraint tax and
    tool-call suppression to. Included to be measured, not to be deployed."""

    name = "global_schema"

    def generate(
        self,
        prefill_ids: Sequence[int],
        *,
        tools: Iterable[Any],
        client: CompletionClient,
        max_tokens: int,
        cancel: Any = None,
        on_delta: Callable[[str, str], None] | None = None,
    ) -> DecodeResult:
        tool_list = list(tools)
        extra = {"json_schema": union_schema(tool_list)} if tool_list else None
        with _Clock() as clock:
            tokens, _, raw = self._collect(
                client,
                prefill_ids,
                max_tokens=max_tokens,
                extra_body=extra,
                cancel=cancel,
                on_delta=on_delta,
                stop_on_commentary=False,
            )
        return DecodeResult(
            tokens=tokens,
            raw=raw,
            strategy=self.name,
            phases=[
                PhaseRecord("full", len(tokens), constrained=True, latency_ms=clock.ms)
            ],
        )


class ChannelScopedStrategy(DecodingStrategy):
    """Channel-scoped constrained decoding.

    `inject_header=True` is CSCD-I: the orchestrator writes the canonical header
    and the model supplies only the tool name, so a malformed header is
    unreachable rather than improbable. `inject_header=False` is CSCD-G: the
    model writes the header tail under a grammar that admits only well-formed
    recipients.
    """

    def __init__(
        self,
        enc: Any,
        specials: SpecialTokens | None = None,
        *,
        inject_header: bool = True,
    ) -> None:
        super().__init__(enc, specials)
        self.inject_header = inject_header
        self.name = "cscd_i" if inject_header else "cscd_g"

    def generate(
        self,
        prefill_ids: Sequence[int],
        *,
        tools: Iterable[Any],
        client: CompletionClient,
        max_tokens: int,
        cancel: Any = None,
        on_delta: Callable[[str, str], None] | None = None,
    ) -> DecodeResult:
        tool_list = list(tools)
        by_name = {getattr(t, "name", ""): t for t in tool_list}
        prefill = list(prefill_ids)
        phases: list[PhaseRecord] = []
        injected = 0

        # --- Phase A: unconstrained, halting at a commentary onset ----------
        rec = self._recognizer()
        with _Clock() as clock:
            body, halted, raw_a = self._collect(
                client,
                prefill,
                max_tokens=max_tokens,
                extra_body=None,
                cancel=cancel,
                on_delta=on_delta,
                stop_on_commentary=True,
                recognizer=rec,
            )
        phases.append(PhaseRecord("A", len(body), constrained=False, latency_ms=clock.ms))

        # No tool call began: this was a final answer (or the turn was cancelled).
        if not halted or not by_name:
            return DecodeResult(
                tokens=body, raw=raw_a, strategy=self.name, phases=phases,
                injected_tokens=0,
            )

        # --- Phase B: the recipient, constrained to the registry ------------
        grammar = (
            tool_name_grammar(by_name)
            if self.inject_header
            else self._header_grammar(by_name)
        )
        lead = self._encode(" to=functions.") if self.inject_header else []
        with _Clock() as clock:
            name_tokens, _, _raw_b = self._collect(
                client,
                prefill + body + lead,
                max_tokens=_NAME_BUDGET,
                extra_body={"grammar": grammar},
                cancel=cancel,
                on_delta=None,
                stop_on_commentary=False,
            )
        clean_name_tokens = self._strip_specials(name_tokens)
        produced = self.enc.decode(clean_name_tokens) if clean_name_tokens else ""
        recipient = self._resolve(produced, by_name)
        phases.append(
            PhaseRecord(
                "B", len(name_tokens), constrained=True,
                latency_ms=clock.ms, recipient=recipient,
            )
        )

        if recipient is None:
            # The server did not honour the grammar. Degrade to the unconstrained
            # continuation rather than emitting a header for a tool that does not
            # exist, and let the caller's parser see whatever the model produces.
            log.warning(
                "CSCD: phase B produced %r, which is not a registered tool; "
                "falling back to unconstrained continuation", produced
            )
            with _Clock() as clock:
                tail, _, raw_fb = self._collect(
                    client, prefill + body, max_tokens=max_tokens,
                    extra_body=None, cancel=cancel, on_delta=on_delta,
                    stop_on_commentary=False,
                )
            phases.append(
                PhaseRecord("fallback", len(tail), constrained=False, latency_ms=clock.ms)
            )
            return DecodeResult(
                tokens=body + tail, strategy=self.name, phases=phases,
                raw={**raw_fb, "cscd_fallback": True},
            )

        # --- Phase B': the canonical header -------------------------------
        # Re-encoded as one string so the assembled sequence is tokenised the way
        # the model would have tokenised it, rather than as the concatenation of
        # separately-encoded fragments.
        header = self._encode(f" to=functions.{recipient} <|constrain|>json<|message|>")
        assembled = body + header
        if self.inject_header:
            injected = len(header)
        else:
            injected = len(self._encode(" <|constrain|>json<|message|>"))
        phases.append(
            PhaseRecord("B'", len(header), constrained=True, latency_ms=0.0,
                        recipient=recipient, request=False)
        )

        # --- Phase C: the argument body, under that one tool's schema -------
        schema = schema_for_tool(by_name[recipient])
        with _Clock() as clock:
            args_tokens, _, raw_c = self._collect(
                client,
                prefill + assembled,
                max_tokens=max_tokens,
                extra_body={"json_schema": schema},
                cancel=cancel,
                on_delta=None,
                stop_on_commentary=False,
            )
        phases.append(
            PhaseRecord("C", len(args_tokens), constrained=True, latency_ms=clock.ms,
                        recipient=recipient)
        )

        # The constrained decode stops as soon as the schema is satisfied and
        # llama.cpp appends an end-of-generation token. Drop whatever terminator
        # it chose and emit the canonical <|call|> ourselves, so the assembled
        # completion is well-formed regardless of which token the build uses.
        tokens = assembled + self._strip_specials(args_tokens) + [self.specials.call]
        injected += 1

        return DecodeResult(
            tokens=tokens,
            strategy=self.name,
            phases=phases,
            injected_tokens=injected,
            raw={**raw_c, "recipient": recipient},
        )

    # --- helpers -----------------------------------------------------------
    def _header_grammar(self, names: Iterable[str]) -> str:
        """CSCD-G: a grammar over the whole header tail, so the model writes
        ` to=functions.<name> ` itself but cannot write anything else."""
        alternatives = " | ".join(f'"{n}"' for n in sorted(names) if n)
        return f'root ::= " to=functions." ({alternatives}) " "\n'

    @staticmethod
    def _resolve(produced: str, by_name: dict[str, Any]) -> str | None:
        """Map a Phase-B output to a registered tool name, or None."""
        text = produced.strip()
        if text in by_name:
            return text
        # CSCD-G produces the whole tail; extract the recipient from it.
        for part in text.replace("\n", " ").split():
            candidate = part[3:] if part.startswith("to=") else part
            candidate = candidate.rsplit(".", 1)[-1].strip()
            if candidate in by_name:
                return candidate
        return None


def build(arm: str, enc: Any, specials: SpecialTokens | None = None) -> DecodingStrategy:
    """Construct the strategy for an experimental arm name."""
    arms: dict[str, Callable[[], DecodingStrategy]] = {
        "unconstrained": lambda: UnconstrainedStrategy(enc, specials),
        "global_schema": lambda: GlobalSchemaStrategy(enc, specials),
        "cscd_i": lambda: ChannelScopedStrategy(enc, specials, inject_header=True),
        "cscd_g": lambda: ChannelScopedStrategy(enc, specials, inject_header=False),
    }
    if arm not in arms:
        raise ValueError(f"unknown decoding arm {arm!r}; expected one of {sorted(arms)}")
    return arms[arm]()
