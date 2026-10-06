"""End-to-end wiring: the real turn loop driving real tools through CSCD.

No model server. The decoder is backed by a ReplayClient, so this exercises
run_turn -> Decoder -> ChannelScopedStrategy -> Harmony parse -> tool dispatch
-> history, which is the path every measured run will take.
"""

import pytest

from agent import loop
from agent.decoding import Decoder, ReplayClient, build
from agent.sandbox import Sandbox
from agent.tools import default_registry
from agent.trace import Trace


@pytest.fixture
def project(tmp_path):
    (tmp_path / "loop.py").write_text(
        "def run_turn():\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    return tmp_path


def decoder_for(enc, responses, trace=None):
    return Decoder(
        strategy=build("cscd_i", enc),
        client=ReplayClient(responses),
        trace=trace,
    )


def test_cscd_turn_reads_a_file_and_answers(enc, E, project):
    """One tool call, then a final answer — the ordinary case."""
    dec = decoder_for(enc, [
        # turn 1: reasoning, then a commentary onset
        E("<|channel|>analysis<|message|>I should read the file<|end|>"
          "<|start|>assistant<|channel|>commentary"),
        E("read"),
        E('{"path":"loop.py"}'),
        # turn 2: the answer
        E("<|channel|>final<|message|>It defines run_turn, which returns 1.<|return|>"),
    ])

    events = []
    result, history = loop.run_turn(
        "What does loop.py do?",
        [],
        default_registry(),
        Sandbox(project),
        decoder=dec,
        on_event=events.append,
        max_turns=5,
    )

    assert result.reason == "completed"
    assert "run_turn" in result.answer

    # The tool actually ran against the real sandbox.
    tool_results = [e for e in events if e.get("role") == "tool"]
    assert len(tool_results) == 1
    assert "def run_turn" in tool_results[0]["content"]
    assert tool_results[0]["recipient"] == "functions.read"


def test_trace_records_the_decode_phases_and_call_provenance(enc, E, project, tmp_path):
    trace_path = tmp_path / "run.jsonl"
    with Trace(trace_path) as tr:
        dec = decoder_for(enc, [
            E("<|channel|>analysis<|message|>look<|end|>"
              "<|start|>assistant<|channel|>commentary"),
            E("read"),
            E('{"path":"loop.py"}'),
            E("<|channel|>final<|message|>done<|return|>"),
        ], trace=tr)
        loop.run_turn("q", [], default_registry(), Sandbox(project),
                      decoder=dec, trace=tr, max_turns=5)

    from agent.trace import read_trace
    records = read_trace(trace_path)
    phases = [r for r in records if r.get("event") == "decode_phase"]
    # Turn 1 is a tool call (A, B, B', C); turn 2 is a final answer, which exits
    # in phase A because no commentary onset occurs.
    assert [p["phase"] for p in phases] == ["A", "B", "B'", "C", "A"]
    # Every unconstrained phase is a reasoning or answer phase, and every
    # constrained one is a header or argument phase. That is the paper's claim,
    # asserted rather than described.
    assert [p["constrained"] for p in phases] == [False, True, True, True, False]

    calls = [r for r in records if r.get("event") == "tool_call"]
    assert [c["source"] for c in calls] == ["header"]
    assert records[-1]["counters"]["tool_calls"] == 1
    assert records[-1]["counters"]["leaked_call_dispatches"] == 0


def test_malformed_header_never_reaches_the_dispatcher(enc, E, project):
    """The guarantee, end to end: the model's duplicated recipient and unknown
    tool are discarded, and a valid call is dispatched instead."""
    dec = decoder_for(enc, [
        E("<|channel|>analysis<|message|>think<|end|>"
          "<|start|>assistant<|channel|>commentary to=functions.bogus to=functions.bogus "
          "<|constrain|>json<|message|>garbage<|call|>"),
        E("read"),
        E('{"path":"README.md"}'),
        E("<|channel|>final<|message|>A demo heading.<|return|>"),
    ])

    events = []
    result, _ = loop.run_turn("q", [], default_registry(), Sandbox(project),
                              decoder=dec, on_event=events.append, max_turns=5)

    assert result.reason == "completed"
    errors = [e for e in events
              if e.get("role") == "tool" and "unknown tool" in (e.get("content") or "")]
    assert not errors, "a bogus recipient reached the dispatcher"
    tool_results = [e for e in events if e.get("role") == "tool"]
    assert "# demo" in tool_results[0]["content"]


def test_unconstrained_baseline_path_is_untouched(enc, E, project):
    """decoder=None keeps the original single-request path, so the baseline arm
    is the pre-existing code rather than a reimplementation of it."""
    import agent.inference as inference

    calls = {"n": 0}

    def fake_complete(prefill_ids, stop_ids=None, max_tokens=None, **kw):
        calls["n"] += 1
        return E("<|channel|>final<|message|>plain answer<|return|>"), {}

    original = inference.complete
    inference.complete = fake_complete
    try:
        result, _ = loop.run_turn("q", [], default_registry(), Sandbox(project),
                                  decoder=None, stream=False, max_turns=3)
    finally:
        inference.complete = original

    assert calls["n"] == 1, "the baseline must issue exactly one request"
    assert result.answer == "plain answer"
