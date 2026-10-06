"""Recognizer tests — the control decision CSCD depends on.

The commentary-onset test is the important one: if the recognizer fires late the
model has already written a recipient and header injection is pointless; if it
fires early or spuriously the agent constrains a reasoning message.
"""

from agent.decoding import Event, HarmonyRecognizer, Region


def feed(rec, ids):
    """Push every id, returning the list of (index, event) pairs emitted."""
    events = []
    for i, t in enumerate(ids):
        ev = rec.push(t)
        if ev is not None:
            events.append((i, ev))
    return events


def test_analysis_message_round_trip(enc, E):
    rec = HarmonyRecognizer.from_encoding(enc)
    ids = E("<|channel|>analysis<|message|>let me look at the loop<|end|>")
    events = feed(rec, ids)

    assert Event.CHANNEL_DECIDED in [e for _, e in events]
    assert Event.COMMENTARY_ONSET not in [e for _, e in events]
    assert rec.messages[-1]["channel"] == "analysis"
    assert rec.messages[-1]["body"] == "let me look at the loop"
    assert rec.messages[-1]["recipient"] is None


def test_final_message_finishes(enc, E):
    rec = HarmonyRecognizer.from_encoding(enc)
    feed(rec, E("<|channel|>final<|message|>the answer<|return|>"))
    assert rec.region is Region.FINISHED
    assert rec.messages[-1]["channel"] == "final"
    assert rec.messages[-1]["body"] == "the answer"


def test_commentary_onset_fires_before_recipient(enc, E):
    """The hand-off must happen while the recipient is still unwritten."""
    rec = HarmonyRecognizer.from_encoding(enc)
    ids = E('<|channel|>commentary to=functions.read <|constrain|>json'
            '<|message|>{"path":"a.py"}<|call|>')

    onset_at = None
    for i, t in enumerate(ids):
        if rec.push(t) is Event.COMMENTARY_ONSET:
            onset_at = i
            break

    assert onset_at is not None, "commentary onset was never detected"
    # Everything consumed so far must decode to just the channel marker + name.
    consumed = enc.decode(ids[: onset_at + 1])
    assert consumed == "<|channel|>commentary"
    assert "to=" not in consumed


def test_tool_call_header_parsed(enc, E):
    rec = HarmonyRecognizer.from_encoding(enc)
    feed(rec, E('<|channel|>commentary to=functions.read <|constrain|>json'
                '<|message|>{"path":"a.py"}<|call|>'))
    assert rec.recipient == "functions.read"
    assert rec.constrained_json is True
    assert rec.messages[-1]["body"] == '{"path":"a.py"}'


def test_duplicated_recipient_takes_first(enc, E):
    """The most common observed malformation. Taking the first `to=` is what
    makes it recoverable at all."""
    rec = HarmonyRecognizer.from_encoding(enc)
    feed(rec, E('<|channel|>commentary to=functions.read to=functions.read '
                '<|constrain|>json<|message|>{"path":"a.py"}<|call|>'))
    assert rec.recipient == "functions.read"


def test_multi_message_completion(enc, E):
    """analysis, then a tool call, in one completion."""
    rec = HarmonyRecognizer.from_encoding(enc)
    feed(rec, E("<|channel|>analysis<|message|>think<|end|>"
                "<|start|>assistant<|channel|>commentary to=functions.grep "
                '<|constrain|>json<|message|>{"pattern":"x"}<|call|>'))
    assert [m["channel"] for m in rec.messages] == ["analysis", "commentary"]
    assert rec.messages[-1]["recipient"] == "functions.grep"


def test_unknown_channel_does_not_hang(enc, E):
    """A malformed channel name must not leave the recognizer stuck collecting
    name tokens forever; it should decide and move on so the caller can fall back."""
    rec = HarmonyRecognizer.from_encoding(enc)
    feed(rec, E("<|channel|>finl<|message|>oops<|end|>"))
    assert rec.channel not in (None, "")
    assert rec.messages[-1]["body"] == "oops"


def test_push_after_finish_is_safe(enc, E):
    rec = HarmonyRecognizer.from_encoding(enc)
    ids = E("<|channel|>final<|message|>done<|return|>")
    feed(rec, ids)
    assert rec.push(ids[0]) is None
    assert rec.region is Region.FINISHED


def test_is_tool_call_predicate(enc, E):
    rec = HarmonyRecognizer.from_encoding(enc)
    feed(rec, E("<|channel|>analysis<|message|>x<|end|>"))
    assert rec.is_tool_call() is False

    rec2 = HarmonyRecognizer.from_encoding(enc)
    feed(rec2, E('<|channel|>commentary to=functions.read <|constrain|>json'
                 '<|message|>{}<|call|>'))
    assert rec2.is_tool_call() is True
