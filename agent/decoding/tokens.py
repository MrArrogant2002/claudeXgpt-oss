"""Harmony special tokens, resolved from the live encoding.

Resolved at construction rather than hardcoded so the module stays correct if the
vocabulary changes, and so tests can build an instance without importing the rest
of the agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Channel names Harmony defines. Anything else in a header is malformed.
KNOWN_CHANNELS = ("analysis", "commentary", "final")


@dataclass(frozen=True)
class SpecialTokens:
    start: int
    end: int
    message: int
    channel: int
    constrain: int
    call: int
    ret: int

    @classmethod
    def from_encoding(cls, enc: Any) -> "SpecialTokens":
        def one(text: str) -> int:
            ids = enc.encode(text, allowed_special="all")
            if len(ids) != 1:
                raise ValueError(
                    f"expected {text!r} to be a single token, got {ids!r}"
                )
            return ids[0]

        return cls(
            start=one("<|start|>"),
            end=one("<|end|>"),
            message=one("<|message|>"),
            channel=one("<|channel|>"),
            constrain=one("<|constrain|>"),
            call=one("<|call|>"),
            ret=one("<|return|>"),
        )

    @property
    def terminators(self) -> frozenset[int]:
        """Tokens that end a message body."""
        return frozenset({self.end, self.call, self.ret})
