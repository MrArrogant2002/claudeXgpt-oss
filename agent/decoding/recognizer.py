"""Incremental Harmony region recognizer (build plan, Phase 4).

Channel-scoped constrained decoding needs to know, *while tokens are still being
produced*, which region of a Harmony completion the model is currently in. In
particular it must detect that a commentary-channel header has begun before the
model emits the recipient, so the orchestrator can take over and emit a canonical
header itself.

This is deliberately separate from `harmony_codec.StreamDecoder`: that one exists
to drive a live display and tolerates being wrong, whereas this one gates a
control decision and must not be.

Regions of an assistant completion:

    <|channel|>analysis<|message|> … free text …                      <|end|>
    <|channel|>commentary to=functions.read <|constrain|>json<|message|>{…}<|call|>
    <|channel|>final<|message|> … answer …                            <|return|>
                ^^^^^^^^^^      ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                CHANNEL_NAME    HEADER                                 BODY

`commentary` is two tokens in the gpt-oss vocabulary, so the channel cannot be
decided from a single token id; the recognizer decodes the accumulated name
instead and decides as soon as it equals a known channel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .tokens import KNOWN_CHANNELS, SpecialTokens


class Region(Enum):
    """Where the recognizer currently is in the completion."""

    PREAMBLE = "preamble"       # before any <|channel|>
    CHANNEL_NAME = "channel"    # accumulating the channel name
    HEADER = "header"           # after the name, before <|message|>
    BODY = "body"               # inside the message body
    FINISHED = "finished"       # a stop token was consumed


class Event(Enum):
    """Emitted by `push` when the recognizer crosses a boundary worth acting on."""

    CHANNEL_DECIDED = "channel_decided"
    COMMENTARY_ONSET = "commentary_onset"   # the CSCD hand-off point
    HEADER_COMPLETE = "header_complete"
    MESSAGE_COMPLETE = "message_complete"
    FINISHED = "finished"


@dataclass
class HarmonyRecognizer:
    """Push output token ids one at a time; read `region`, `channel`, `recipient`.

    `push` returns an `Event` when a boundary is crossed, else None. The object
    tolerates being pushed tokens after FINISHED (it simply keeps returning None),
    so a caller collecting a whole completion need not special-case the end.
    """

    enc: Any
    specials: SpecialTokens

    region: Region = Region.PREAMBLE
    channel: str | None = None
    recipient: str | None = None
    constrained_json: bool = False

    _name_tokens: list[int] = field(default_factory=list, repr=False)
    _header_tokens: list[int] = field(default_factory=list, repr=False)
    _body_tokens: list[int] = field(default_factory=list, repr=False)
    _messages: list[dict[str, Any]] = field(default_factory=list, repr=False)

    @classmethod
    def from_encoding(cls, enc: Any) -> "HarmonyRecognizer":
        return cls(enc=enc, specials=SpecialTokens.from_encoding(enc))

    # --- public state ------------------------------------------------------
    @property
    def messages(self) -> list[dict[str, Any]]:
        """Completed messages seen so far: {channel, recipient, body, stop}."""
        return list(self._messages)

    @property
    def body_text(self) -> str:
        return self._decode(self._body_tokens)

    def is_tool_call(self) -> bool:
        return self.channel == "commentary" and self.recipient is not None

    # --- the machine -------------------------------------------------------
    def push(self, token_id: int) -> Event | None:
        s = self.specials

        if self.region is Region.FINISHED:
            return None

        # A new message may begin at any point: <|start|>assistant<|channel|>…
        if token_id == s.channel:
            self._name_tokens.clear()
            self._header_tokens.clear()
            self.channel = None
            self.recipient = None
            self.constrained_json = False
            self.region = Region.CHANNEL_NAME
            return None

        if self.region is Region.PREAMBLE:
            return None

        if self.region is Region.CHANNEL_NAME:
            return self._push_channel_name(token_id)

        if self.region is Region.HEADER:
            return self._push_header(token_id)

        if self.region is Region.BODY:
            return self._push_body(token_id)

        return None

    # --- region handlers ---------------------------------------------------
    def _push_channel_name(self, token_id: int) -> Event | None:
        s = self.specials

        # A well-formed header ends the name at <|message|> (no recipient) or
        # continues into the header. Handle both without waiting for more tokens.
        if token_id == s.message:
            self._decide_channel(self._decode(self._name_tokens))
            self.region = Region.BODY
            self._body_tokens.clear()
            return Event.HEADER_COMPLETE

        self._name_tokens.append(token_id)
        text = self._decode(self._name_tokens).strip()

        if text in KNOWN_CHANNELS:
            self._decide_channel(text)
            self.region = Region.HEADER
            self._header_tokens.clear()
            # The commentary onset is the hand-off point for CSCD: the model has
            # committed to a tool call but has not yet written the recipient.
            if text == "commentary":
                return Event.COMMENTARY_ONSET
            return Event.CHANNEL_DECIDED

        # Not yet a complete known name. If what we have can no longer become
        # one, the model has gone off-format; record it and keep going so the
        # caller can fall back rather than crash.
        if text and not any(c.startswith(text) for c in KNOWN_CHANNELS):
            self._decide_channel(text)
            self.region = Region.HEADER
            self._header_tokens.clear()
            return Event.CHANNEL_DECIDED
        return None

    def _push_header(self, token_id: int) -> Event | None:
        s = self.specials
        if token_id == s.constrain:
            self.constrained_json = True
            return None
        if token_id == s.message:
            self._parse_header(self._decode(self._header_tokens))
            self.region = Region.BODY
            self._body_tokens.clear()
            return Event.HEADER_COMPLETE
        self._header_tokens.append(token_id)
        return None

    def _push_body(self, token_id: int) -> Event | None:
        s = self.specials
        if token_id in s.terminators:
            self._messages.append(
                {
                    "channel": self.channel,
                    "recipient": self.recipient,
                    "body": self.body_text,
                    "stop": token_id,
                }
            )
            if token_id == s.ret:
                self.region = Region.FINISHED
                return Event.FINISHED
            self.region = Region.PREAMBLE
            return Event.MESSAGE_COMPLETE
        self._body_tokens.append(token_id)
        return None

    # --- helpers -----------------------------------------------------------
    def _decide_channel(self, text: str) -> None:
        self.channel = text.strip() or None

    def _parse_header(self, text: str) -> None:
        """Extract the recipient from a header body such as
        ` to=functions.read ` . Takes the FIRST `to=`, which is what makes a
        duplicated-recipient header (the most common malformation) recoverable."""
        for part in text.replace("\n", " ").split():
            if part.startswith("to="):
                self.recipient = part[3:].strip() or None
                return

    def _decode(self, ids: list[int]) -> str:
        if not ids:
            return ""
        try:
            return self.enc.decode(ids)
        except Exception:
            # A partial multi-byte sequence can fail to decode mid-stream; the
            # next token completes it.
            return ""
