"""Control-token provenance gate — the dispatch-time trust boundary.

The orchestrator may not derive a tool invocation from text. It may derive one
only from control tokens whose provenance it can establish. This module decides
that, over the raw output token identifiers of a single completion.

Why this is at the token level
------------------------------
A Harmony tool-call header interleaves two kinds of control field:

    <|channel|>  commentary  to=functions.read  <|constrain|>  json  <|message|>
       [R]           [T]            [T]              [R]        [T]      [R]

Fields marked [R] are single reserved vocabulary identifiers. A copy of one
built out of untrusted text is a *different* token sequence that decodes to the
same bytes, so a forgery is detectable — but only if the check runs on
identifiers rather than on decoded text. Every text-level check is blind here by
construction.

Fields marked [T] are ordinary text. `commentary` is two tokens and
`to=functions.read` is four; neither has a reserved identifier, so a forgery of
them is indistinguishable from the genuine field at every level available to the
orchestrator. The recipient is in this class, and it is the field that names the
tool to run. Since authentication is impossible for [T], this gate instead
confines it: the recipient must equal a registered tool name exactly.

The four conditions
-------------------
1. **Reserved-identifier provenance.** Every [R] field is present as that
   marker's reserved identifier, not as any other sequence decoding to the same
   bytes.
2. **Canonical ordering.** Those identifiers occur in the order the format
   specifies, with no reserved token inside a message body and no repetition
   inside a header.
3. **Turn provenance.** Candidates are drawn only from the identifiers returned
   by the current completion. Enforced structurally: this module is given one
   completion's output tokens and has no access to history or tool results.
4. **Registry exactness.** The recipient matches a registered tool by exact
   string equality — no prefix, suffix, case, or edit-distance matching.

A call failing any condition is not dispatched. It is returned to the model as
data, so a genuine malformation costs a turn rather than crashing the agent.

What this gate does not do
--------------------------
It establishes that a call corresponds to control tokens the model actually
emitted. It does **not** judge whether the call should be allowed: a model that
reads a hostile file and then genuinely decides to call `bash` produces a
well-formed call, and this gate admits it. That is a question about intent and
authorisation, and it belongs to the permission engine and the containment layer
above and below. Conflating the two would overstate what a provenance check can
buy.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence

from . import harmony_codec as hc

log = logging.getLogger(__name__)

# Channel that may carry a tool call. A header naming any other channel is not
# a call, and a header naming a channel Harmony does not define is malformed.
TOOL_CHANNEL = "commentary"
KNOWN_CHANNELS = frozenset({"analysis", "commentary", "final"})

# The only constraint type Harmony specifies for a tool-call argument body.
CONSTRAINT_TYPE = "json"

# The recipient's required shape. Anchored at both ends: a header carrying
# anything beyond one well-formed recipient is rejected rather than cleaned up.
_RECIPIENT_RE = re.compile(r"^to=functions\.([A-Za-z_][A-Za-z0-9_]*)$")


class Reject(str, Enum):
    """Why a candidate was not admitted. These double as measurement categories."""

    NO_RESERVED_HEADER = "no_reserved_header"
    RESERVED_TOKEN_IN_BODY = "reserved_token_in_body"
    NON_CANONICAL_HEADER = "non_canonical_header"
    UNKNOWN_CHANNEL = "unknown_channel"
    DUPLICATE_RECIPIENT = "duplicate_recipient"
    MALFORMED_RECIPIENT = "malformed_recipient"
    UNKNOWN_TOOL = "unknown_tool"
    BAD_CONSTRAINT_TYPE = "bad_constraint_type"
    MISSING_CALL_TERMINATOR = "missing_call_terminator"
    INVALID_JSON_ARGUMENTS = "invalid_json_arguments"


@dataclass(frozen=True)
class AdmittedCall:
    """A call that satisfied all four conditions and may be dispatched."""

    name: str
    recipient: str
    arguments: dict
    span: tuple[int, int]


@dataclass(frozen=True)
class Rejection:
    """A candidate that did not. `detail` is safe to log; it never carries body text."""

    reason: Reject
    detail: str
    span: tuple[int, int]


@dataclass(frozen=True)
class GateResult:
    calls: list[AdmittedCall] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)

    @property
    def admitted(self) -> bool:
        return bool(self.calls)

    def summary(self) -> str:
        if self.calls:
            return "admitted " + ", ".join(c.name for c in self.calls)
        if self.rejections:
            return "rejected " + ", ".join(r.reason.value for r in self.rejections)
        return "no tool call"


# --- reserved identifiers, resolved from the live encoding ------------------
# Resolved rather than hardcoded so the gate stays correct if the vocabulary
# changes, and so a vocabulary that fails to provide a single identifier for a
# marker is a loud error rather than a silent weakening of condition 1.
_MARKERS = ("start", "end", "message", "channel", "constrain", "call", "return")


def _resolve() -> dict[str, int]:
    enc = hc.encoding()
    out: dict[str, int] = {}
    for name in _MARKERS:
        ids = enc.encode(f"<|{name}|>", allowed_special="all")
        if len(ids) != 1:
            raise RuntimeError(
                f"<|{name}|> is not a single reserved token in this vocabulary "
                f"(got {ids!r}); the provenance check cannot be enforced"
            )
        out[name] = ids[0]
    return out


_ID = _resolve()
_TERMINATORS = frozenset({_ID["end"], _ID["call"], _ID["return"]})
_RESERVED = frozenset(_ID.values())


def reserved_ids() -> dict[str, int]:
    """The resolved reserved identifiers, for traces and tests."""
    return dict(_ID)


# --- the scanner ------------------------------------------------------------


@dataclass
class _Candidate:
    """One message found in the token stream, with its header and body spans."""

    start: int
    header: list[int] = field(default_factory=list)
    body: list[int] = field(default_factory=list)
    saw_constrain: bool = False
    constrain_text: list[int] = field(default_factory=list)
    terminator: int | None = None
    fault: Reject | None = None
    end: int = 0


def _scan(tokens: Sequence[int]) -> list[_Candidate]:
    """Split a completion into messages, enforcing condition 2 as it goes.

    Hand-written rather than delegated to the parser in `harmony_codec`, because
    that parser is deliberately tolerant: it salvages malformed headers so a bad
    completion does not waste a turn. Tolerance is correct for keeping history
    renderable and wrong for deciding what to execute, so the two must not share
    an implementation.
    """
    out: list[_Candidate] = []
    cur: _Candidate | None = None
    region = "outside"  # outside | header | constrain | body

    for i, tok in enumerate(tokens):
        if region == "outside":
            if tok == _ID["channel"]:
                cur = _Candidate(start=i)
                region = "header"
            # <|start|> and the role tokens that follow it are skipped; a role
            # is not a control field this gate authenticates, because a call is
            # identified by its channel and recipient.
            continue

        assert cur is not None

        if region in ("header", "constrain"):
            if tok == _ID["message"]:
                region = "body"
            elif tok == _ID["constrain"]:
                if cur.saw_constrain:  # repetition inside a header
                    cur.fault = cur.fault or Reject.NON_CANONICAL_HEADER
                cur.saw_constrain = True
                region = "constrain"
            elif tok in _RESERVED:
                # Any other reserved token inside a header is non-canonical.
                cur.fault = cur.fault or Reject.NON_CANONICAL_HEADER
                cur.end = i
                out.append(cur)
                cur, region = None, "outside"
            elif region == "constrain":
                cur.constrain_text.append(tok)
            else:
                cur.header.append(tok)
            continue

        # region == "body"
        if tok in _TERMINATORS:
            cur.terminator = tok
            cur.end = i
            out.append(cur)
            cur, region = None, "outside"
        elif tok in _RESERVED:
            # A reserved token inside a body means the stream is not what it
            # appears to be. Refuse the message rather than guess where it ends.
            cur.fault = cur.fault or Reject.RESERVED_TOKEN_IN_BODY
            cur.end = i
            out.append(cur)
            cur, region = None, "outside"
        else:
            cur.body.append(tok)

    if cur is not None:  # ran off the end without a terminator
        cur.end = len(tokens)
        cur.fault = cur.fault or Reject.MISSING_CALL_TERMINATOR
        out.append(cur)
    return out


def _decode(tokens: Iterable[int]) -> str:
    ids = list(tokens)
    if not ids:
        return ""
    try:
        return hc.encoding().decode(ids)
    except Exception as exc:  # a partial multi-byte sequence, or a binding error
        log.debug("provenance: header decode failed: %s", type(exc).__name__)
        return ""


def _check_header(text: str, known: frozenset[str]) -> tuple[str | None, Rejection | None]:
    """Validate a header's textual control fields. Returns (tool name, rejection)."""
    parts = text.split()
    if not parts:
        return None, Rejection(Reject.NON_CANONICAL_HEADER, "empty header", (0, 0))

    channel = parts[0]
    if channel not in KNOWN_CHANNELS:
        return None, Rejection(Reject.UNKNOWN_CHANNEL, f"channel={channel!r}", (0, 0))
    if channel != TOOL_CHANNEL:
        return None, None  # a legitimate non-call message; not a rejection

    recipients = [p for p in parts[1:] if p.startswith("to=")]
    if len(recipients) == 0:
        return None, Rejection(Reject.MALFORMED_RECIPIENT, "no recipient", (0, 0))
    if len(recipients) > 1:
        # The malformation gpt-oss produces most often. A tolerant parser takes
        # the first and proceeds; taking either is a guess about which one the
        # model meant, so the gate takes neither.
        return None, Rejection(
            Reject.DUPLICATE_RECIPIENT, f"{len(recipients)} recipients", (0, 0)
        )

    extra = [p for p in parts[1:] if not p.startswith("to=")]
    if extra:
        return None, Rejection(
            Reject.NON_CANONICAL_HEADER, f"unexpected header fields: {len(extra)}", (0, 0)
        )

    m = _RECIPIENT_RE.match(recipients[0])
    if m is None:
        return None, Rejection(Reject.MALFORMED_RECIPIENT, "shape", (0, 0))

    name = m.group(1)
    if name not in known:  # condition 4: exact equality, no fuzzy matching
        return None, Rejection(Reject.UNKNOWN_TOOL, f"tool={name!r}", (0, 0))
    return name, None


def admit(output_tokens: Sequence[int], registry) -> GateResult:
    """Decide which tool calls in one completion may be dispatched.

    `output_tokens` must be the identifiers returned by the current turn's
    inference request and nothing else. That restriction is condition 3, and it
    is what makes the gate sound: text that entered the context as a tool result
    or as history is not in this sequence, so it cannot produce a candidate
    however it is spelled.
    """
    known = frozenset(t.name for t in registry.all())
    calls: list[AdmittedCall] = []
    rejections: list[Rejection] = []

    for cand in _scan(output_tokens):
        span = (cand.start, cand.end)

        header_text = _decode(cand.header)
        name, rejection = _check_header(header_text, known)

        if rejection is not None:
            rejections.append(
                Rejection(rejection.reason, rejection.detail, span)
            )
            continue
        if name is None:
            continue  # analysis or final message; nothing to dispatch

        # A fault found while scanning outranks the header being well formed:
        # the span is not trustworthy even if its header reads correctly.
        if cand.fault is not None:
            rejections.append(Rejection(cand.fault, f"tool={name}", span))
            continue

        if cand.terminator != _ID["call"]:
            # Generalises the parser hardening recommended for the
            # truncation-tolerance attack: a call must be closed by <|call|>,
            # never by end-of-stream and never by another terminator.
            rejections.append(
                Rejection(Reject.MISSING_CALL_TERMINATOR, f"tool={name}", span)
            )
            continue

        if cand.saw_constrain:
            if _decode(cand.constrain_text).strip() != CONSTRAINT_TYPE:
                rejections.append(
                    Rejection(Reject.BAD_CONSTRAINT_TYPE, f"tool={name}", span)
                )
                continue

        raw = _decode(cand.body)
        try:
            args = json.loads(raw) if raw.strip() else {}
        except (json.JSONDecodeError, TypeError, ValueError):
            rejections.append(
                Rejection(Reject.INVALID_JSON_ARGUMENTS, f"tool={name}", span)
            )
            continue
        if not isinstance(args, dict):
            rejections.append(
                Rejection(Reject.INVALID_JSON_ARGUMENTS, f"tool={name}: not an object", span)
            )
            continue

        calls.append(
            AdmittedCall(
                name=name, recipient=f"functions.{name}", arguments=args, span=span
            )
        )

    if rejections:
        log.info(
            "provenance gate rejected %d candidate(s): %s",
            len(rejections),
            ", ".join(r.reason.value for r in rejections),
        )
    return GateResult(calls=calls, rejections=rejections)
