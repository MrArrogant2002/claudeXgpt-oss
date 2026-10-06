"""Decoding-strategy tests.

These exercise the paper's first contribution without a GPU, by replaying token
sequences a server would have produced. The load-bearing test is
`test_cscd_discards_malformed_header_the_model_would_have_written`: it shows the
guarantee is structural, not probabilistic.
"""

from types import SimpleNamespace

import pytest

from agent.decoding import HarmonyRecognizer
from agent.decoding.client import ReplayClient
from agent.decoding.strategies import build


def tool(name, properties, required=()):
    return SimpleNamespace(
        name=name,
        description=f"{name} tool",
        parameters={
            "type": "object",
            "properties": properties,
            "required": list(required),
        },
    )


@pytest.fixture
def tools():
    return [
        tool("read", {"path": {"type": "string"},
                      "start_line": {"type": "integer"}}, ["path"]),
        tool("grep", {"pattern": {"type": "string"},
                      "path": {"type": "string"}}, ["pattern"]),
        tool("glob", {"pattern": {"type": "string"}}, ["pattern"]),
    ]


def decode(enc, ids):
    return enc.decode(ids)


# --- baselines -------------------------------------------------------------

def test_unconstrained_makes_one_unconstrained_call(enc, E, tools):
    client = ReplayClient([E("<|channel|>final<|message|>hello<|return|>")])
    result = build("unconstrained", enc).generate(
        E("<|start|>assistant"), tools=tools, client=client, max_tokens=256
    )
    assert result.round_trips == 1
    assert client.calls[0]["extra_body"] == {}
    assert decode(enc, result.tokens).endswith("hello<|return|>")


def test_global_schema_constrains_the_whole_completion(enc, E, tools):
    client = ReplayClient([E("<|channel|>final<|message|>hi<|return|>")])
    result = build("global_schema", enc).generate(
        E("<|start|>assistant"), tools=tools, client=client, max_tokens=256
    )
    assert result.round_trips == 1
    body = client.calls[0]["extra_body"]
    assert "json_schema" in body
    # The union schema is weaker than a per-recipient one: it must admit every
    # tool's parameters, which is the point of including this arm.
    assert "anyOf" in body["json_schema"]
    assert result.phases[0].constrained is True


# --- CSCD ------------------------------------------------------------------

def test_cscd_leaves_a_final_answer_untouched(enc, E, tools):
    """No tool call began, so no constraint is ever applied."""
    client = ReplayClient([E("<|channel|>final<|message|>the answer<|return|>")])
    result = build("cscd_i", enc).generate(
        E("<|start|>assistant"), tools=tools, client=client, max_tokens=256
    )
    assert result.round_trips == 1
    assert client.calls[0]["extra_body"] == {}
    assert result.injected_tokens == 0
    assert decode(enc, result.tokens) == "<|channel|>final<|message|>the answer<|return|>"


def test_cscd_phase_a_is_never_constrained(enc, E, tools):
    client = ReplayClient([
        E("<|channel|>analysis<|message|>think<|end|>"
          "<|start|>assistant<|channel|>commentary to=functions.read "
          '<|constrain|>json<|message|>{"path":"x"}<|call|>'),
        E("read"),
        E('{"path":"loop.py"}'),
    ])
    build("cscd_i", enc).generate(
        E("<|start|>assistant"), tools=tools, client=client, max_tokens=256
    )
    assert client.calls[0]["extra_body"] == {}, "phase A must carry no grammar"
    assert "grammar" in client.calls[1]["extra_body"], "phase B must constrain the name"
    assert "json_schema" in client.calls[2]["extra_body"], "phase C must constrain args"


def test_cscd_assembles_a_canonical_tool_call(enc, E, tools):
    client = ReplayClient([
        E("<|channel|>analysis<|message|>look at the loop<|end|>"
          "<|start|>assistant<|channel|>commentary"),
        E("read"),
        E('{"path":"agent/loop.py"}'),
    ])
    result = build("cscd_i", enc).generate(
        E("<|start|>assistant"), tools=tools, client=client, max_tokens=256
    )

    text = decode(enc, result.tokens)
    assert text == (
        "<|channel|>analysis<|message|>look at the loop<|end|>"
        "<|start|>assistant<|channel|>commentary to=functions.read "
        '<|constrain|>json<|message|>{"path":"agent/loop.py"}<|call|>'
    )

    # And the existing parser must agree about what it is.
    rec = HarmonyRecognizer.from_encoding(enc)
    for t in result.tokens:
        rec.push(t)
    assert rec.messages[-1]["recipient"] == "functions.read"
    assert rec.messages[-1]["body"] == '{"path":"agent/loop.py"}'


def test_cscd_discards_malformed_header_the_model_would_have_written(enc, E, tools):
    """The guarantee is structural.

    Phase A's replayed response contains a duplicated recipient and an unknown
    tool — exactly the malformations the tolerant parser exists to repair. CSCD
    never consumes them, because it stops at the commentary onset and writes the
    header itself.
    """
    poisoned = (
        "<|channel|>analysis<|message|>think<|end|>"
        "<|start|>assistant<|channel|>commentary to=functions.nope to=functions.nope "
        "<|constrain|>json<|message|>not json at all<|call|>"
    )
    client = ReplayClient([E(poisoned), E("read"), E('{"path":"a.py"}')])
    result = build("cscd_i", enc).generate(
        E("<|start|>assistant"), tools=tools, client=client, max_tokens=256
    )

    text = decode(enc, result.tokens)
    assert "to=functions.nope" not in text
    assert "not json at all" not in text
    assert text.count("to=") == 1, "exactly one recipient, always"
    assert "to=functions.read" in text


def test_cscd_falls_back_when_the_server_ignores_the_grammar(enc, E, tools):
    """A server build that drops `grammar` must degrade, not emit a header for a
    tool that does not exist."""
    client = ReplayClient([
        E("<|channel|>analysis<|message|>t<|end|>"
          "<|start|>assistant<|channel|>commentary"),
        E("not_a_registered_tool"),
        E(" to=functions.read <|constrain|>json<|message|>{}<|call|>"),
    ])
    result = build("cscd_i", enc).generate(
        E("<|start|>assistant"), tools=tools, client=client, max_tokens=256
    )
    assert result.raw.get("cscd_fallback") is True
    assert [p.phase for p in result.phases] == ["A", "B", "fallback"]


def test_cscd_g_constrains_the_header_tail_instead_of_injecting_it(enc, E, tools):
    client = ReplayClient([
        E("<|channel|>analysis<|message|>t<|end|>"
          "<|start|>assistant<|channel|>commentary"),
        E(" to=functions.grep "),
        E('{"pattern":"def run"}'),
    ])
    result = build("cscd_g", enc).generate(
        E("<|start|>assistant"), tools=tools, client=client, max_tokens=256
    )
    grammar = client.calls[1]["extra_body"]["grammar"]
    assert "to=functions." in grammar
    assert '"grep"' in grammar
    assert "to=functions.grep" in decode(enc, result.tokens)
    # CSCD-G lets the model write the recipient, so fewer tokens are injected.
    assert result.injected_tokens < 8


def test_cscd_uses_the_schema_of_the_selected_tool_only(enc, E, tools):
    """Per-recipient schemas are tighter than a union: `read` must not accept
    `pattern`, which a whole-completion schema is obliged to admit."""
    client = ReplayClient([
        E("<|channel|>analysis<|message|>t<|end|>"
          "<|start|>assistant<|channel|>commentary"),
        E("read"),
        E('{"path":"a.py"}'),
    ])
    build("cscd_i", enc).generate(
        E("<|start|>assistant"), tools=tools, client=client, max_tokens=256
    )
    schema = client.calls[2]["extra_body"]["json_schema"]
    assert set(schema["properties"]) == {"path", "start_line"}
    assert "pattern" not in schema["properties"]
    assert schema["additionalProperties"] is False


def test_phase_records_report_round_trips_and_constraint(enc, E, tools):
    client = ReplayClient([
        E("<|channel|>analysis<|message|>t<|end|>"
          "<|start|>assistant<|channel|>commentary"),
        E("glob"),
        E('{"pattern":"**/*.py"}'),
    ])
    result = build("cscd_i", enc).generate(
        E("<|start|>assistant"), tools=tools, client=client, max_tokens=256
    )
    assert [p.phase for p in result.phases] == ["A", "B", "B'", "C"]
    assert [p.constrained for p in result.phases] == [False, True, True, True]
    # Four phases, but only three server requests: the orchestrator emits B'
    # itself. Counting it would overstate CSCD's cost by one request per call.
    assert result.phase_count == 4
    assert result.round_trips == 3
    assert [p.request for p in result.phases] == [True, True, False, True]
    assert result.latency_ms >= 0


# --- regression: trailing end-of-generation tokens --------------------------
# Observed on the GPU box (llama.cpp, gpt-oss-20b, 2026-10-06): a constrained
# generation stops as soon as the grammar or schema is satisfied, and the server
# appends an end-of-generation token. Phase B therefore returns `read<|call|>`
# and Phase C `{...}<|call|>`. Matching on the raw decode treated a perfectly
# conformant response as a failure and sent the whole turn down the fallback path.

def test_phase_b_tolerates_trailing_eog_token(enc, E, tools):
    client = ReplayClient([
        E("<|channel|>analysis<|message|>t<|end|>"
          "<|start|>assistant<|channel|>commentary"),
        E("read<|call|>"),
        E('{"path":"a.py"}<|call|>'),
    ])
    result = build("cscd_i", enc).generate(
        E("<|start|>assistant"), tools=tools, client=client, max_tokens=256
    )
    assert result.raw.get("cscd_fallback") is not True
    assert result.raw.get("recipient") == "read"


def test_assembled_call_has_exactly_one_terminator(enc, E, tools):
    """The server's chosen terminator is dropped and the canonical <|call|>
    emitted, so the completion is well-formed whichever token the build uses."""
    client = ReplayClient([
        E("<|channel|>analysis<|message|>t<|end|>"
          "<|start|>assistant<|channel|>commentary"),
        E("grep<|endoftext|>"),
        E('{"pattern":"def run"}<|endoftext|>'),
    ])
    result = build("cscd_i", enc).generate(
        E("<|start|>assistant"), tools=tools, client=client, max_tokens=256
    )
    text = decode(enc, result.tokens)
    assert text.endswith('{"pattern":"def run"}<|call|>')
    assert "<|endoftext|>" not in text
    assert text.count("<|call|>") == 1


def test_argument_body_keeps_legitimate_angle_brackets(enc, E, tools):
    """Filtering happens at the token-id level, so a body that legitimately
    contains angle brackets survives — a regex over decoded text would not."""
    client = ReplayClient([
        E("<|channel|>analysis<|message|>t<|end|>"
          "<|start|>assistant<|channel|>commentary"),
        E("grep"),
        E('{"pattern":"List<int> items"}<|call|>'),
    ])
    result = build("cscd_i", enc).generate(
        E("<|start|>assistant"), tools=tools, client=client, max_tokens=256
    )
    assert 'List<int> items' in decode(enc, result.tokens)
