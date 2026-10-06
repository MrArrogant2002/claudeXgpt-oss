"""Settings, trace, ledger, schema and containment tests.

These cover the Phase 1/5/6 infrastructure that every reported number depends on.
None of them need a model server.
"""

import json
import sys

import pytest

from agent.containment import (
    ContainmentUnavailable,
    available,
    profile_for,
    wrap_argv,
)
from agent.decoding.gbnf import (
    schema_for_tool,
    tool_name_grammar,
    union_schema,
    validate_against,
)
from agent.ledger import EvidenceLedger, summarise
from agent.settings import AgentSettings, SamplingSettings
from agent.trace import RunCounters, Trace, read_trace


# --- settings --------------------------------------------------------------

def test_settings_are_immutable():
    s = AgentSettings()
    with pytest.raises(Exception):
        s.allow_exec = True  # type: ignore[misc]


def test_with_returns_a_modified_copy():
    base = AgentSettings()
    arm = base.with_(decoding="cscd_i", containment="enforced")
    assert base.decoding == "unconstrained", "the original must not be mutated"
    assert arm.decoding == "cscd_i"
    assert arm.containment == "enforced"


def test_sampling_omits_neutral_values_but_sends_seed():
    body = SamplingSettings(seed=42).to_request()
    assert body["seed"] == 42
    # Neutral knobs are left to the server rather than overridden.
    assert "top_k" not in body and "min_p" not in body and "repeat_penalty" not in body


def test_greedy_arm_is_deterministic_client_side():
    g = SamplingSettings.greedy(seed=5)
    body = g.to_request()
    assert body["temperature"] == 0.0
    assert body["top_k"] == 1
    assert body["seed"] == 5


def test_settings_serialise_for_the_manifest():
    d = AgentSettings().with_(recovery=frozenset({"salvage"})).to_dict()
    assert d["recovery"] == ["salvage"]
    json.dumps(d)  # must be JSON-serialisable or the manifest cannot be written


# --- trace -----------------------------------------------------------------

def test_disabled_trace_is_a_working_no_op():
    t = Trace(None)
    t.manifest(AgentSettings())
    t.event("anything", x=1)
    t.close()  # must not raise


def test_trace_writes_manifest_events_and_summary(tmp_path):
    path = tmp_path / "run.jsonl"
    with Trace(path, run_id="abc") as t:
        t.manifest(AgentSettings().with_(project_root=str(tmp_path)))
        t.event("decode_phase", phase="A")
        t.tool_call(name="read", args={"path": "a.py"}, result="x" * 10)

    records = read_trace(path)
    assert records[0]["kind"] == "manifest"
    assert records[-1]["kind"] == "summary"
    assert all(r["run_id"] == "abc" for r in records)
    assert records[-1]["counters"]["tool_calls"] == 1


def test_tool_call_source_is_recorded_for_the_dispatch_surface(tmp_path):
    """`source` is the dispatch-surface measurement; losing it loses the result."""
    path = tmp_path / "run.jsonl"
    with Trace(path) as t:
        t.tool_call(name="bash", args={"command": "ls"}, result="", source="prose")
        t.tool_call(name="read", args={"path": "a"}, result="", source="header")
    records = read_trace(path)
    calls = [r for r in records if r.get("event") == "tool_call"]
    assert [c["source"] for c in calls] == ["prose", "header"]
    assert records[-1]["counters"]["leaked_call_dispatches"] == 1


def test_trace_never_logs_raw_tool_arguments(tmp_path):
    """Arguments are hashed, not stored: a trace of a private repository is itself
    a disclosure risk, and the metrics only need identity."""
    path = tmp_path / "run.jsonl"
    with Trace(path) as t:
        t.tool_call(name="read", args={"path": "secret/key.pem"}, result="PRIVATE KEY")
    blob = path.read_text(encoding="utf-8")
    assert "secret/key.pem" not in blob
    assert "PRIVATE KEY" not in blob
    assert "args_sha256" in blob


def test_counters_are_per_run_not_global():
    a, b = RunCounters(), RunCounters()
    a.tool_calls += 1
    assert b.tool_calls == 0


def test_read_trace_skips_malformed_lines(tmp_path):
    path = tmp_path / "broken.jsonl"
    path.write_text('{"kind":"event"}\nnot json\n{"kind":"event"}\n', encoding="utf-8")
    assert len(read_trace(path)) == 2


# --- schemas ---------------------------------------------------------------

class _Tool:
    def __init__(self, name, properties, required=()):
        self.name = name
        self.parameters = {
            "type": "object",
            "properties": properties,
            "required": list(required),
        }


def test_tool_name_grammar_is_sorted_and_quoted():
    g = tool_name_grammar(["read", "glob", "grep"])
    assert g.startswith("root ::=")
    assert '"glob" | "grep" | "read"' in g


def test_empty_registry_is_refused():
    """Constraining to nothing would make every token illegal and hang the decode."""
    with pytest.raises(ValueError):
        tool_name_grammar([])


def test_schema_for_tool_forbids_invented_parameters():
    s = schema_for_tool(_Tool("read", {"path": {"type": "string"}}, ["path"]))
    assert s["additionalProperties"] is False


def test_schema_for_tool_does_not_mutate_the_registry():
    t = _Tool("read", {"path": {"type": "string"}})
    before = json.dumps(t.parameters, sort_keys=True)
    schema_for_tool(t)
    assert json.dumps(t.parameters, sort_keys=True) == before


def test_union_schema_is_weaker_than_a_per_recipient_schema():
    """The core argument for CSCD's precision: a union must admit every tool's
    parameters, so it cannot reject one tool's argument supplied to another."""
    read = _Tool("read", {"path": {"type": "string"}}, ["path"])
    grep = _Tool("grep", {"pattern": {"type": "string"}}, ["pattern"])
    wrong = {"pattern": "x"}

    assert validate_against(schema_for_tool(read), wrong), "per-recipient must reject"
    assert not validate_against(union_schema([read, grep]), wrong), "union must admit"


def test_validate_rejects_boolean_as_integer():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}},
              "additionalProperties": False}
    assert validate_against(schema, {"n": True})
    assert not validate_against(schema, {"n": 3})


# --- ledger ----------------------------------------------------------------

def test_ledger_records_and_renders(tmp_path):
    (tmp_path / "a.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    led = EvidenceLedger(tmp_path)
    obs = led.record(tool="read", content="one\ntwo", path="a.py",
                     start_line=1, end_line=2, turn=1)
    assert obs.obs_id == "E1"
    rendered = led.render()
    assert "E1" in rendered and "a.py:1-2" in rendered


def test_verify_confirms_an_unchanged_citation(tmp_path):
    (tmp_path / "a.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    led = EvidenceLedger(tmp_path)
    led.record(tool="read", content="one\ntwo", path="a.py", start_line=1, end_line=2)
    report = led.verify("The header is at the top [E1].")
    assert report.verified == ["E1"]
    assert report.verified_rate == 1.0


def test_verify_detects_a_citation_whose_file_changed(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("one\ntwo\nthree\n", encoding="utf-8")
    led = EvidenceLedger(tmp_path)
    led.record(tool="read", content="one\ntwo", path="a.py", start_line=1, end_line=2)
    f.write_text("CHANGED\ntwo\nthree\n", encoding="utf-8")
    report = led.verify("As shown [E1].")
    assert report.stale == ["E1"] and report.verified == []


def test_verify_flags_a_fabricated_citation(tmp_path):
    led = EvidenceLedger(tmp_path)
    assert led.verify("Obviously true [E7].").unknown == ["E7"]


def test_ledger_survives_compaction_by_construction(tmp_path):
    """The ledger is re-rendered from its own rows, so discarding conversation
    history cannot remove the evidence an answer must cite."""
    (tmp_path / "a.py").write_text("x\n", encoding="utf-8")
    led = EvidenceLedger(tmp_path)
    led.record(tool="read", content="x\n", path="a.py", start_line=1, end_line=1)
    history = ["turn1", "turn2"]
    history.clear()  # a maximally aggressive compaction
    assert "E1" in led.render()
    assert led.verify("see [E1]").verified == ["E1"]


def test_summarise_aggregates_reports(tmp_path):
    (tmp_path / "a.py").write_text("x\n", encoding="utf-8")
    led = EvidenceLedger(tmp_path)
    led.record(tool="read", content="x\n", path="a.py", start_line=1, end_line=1)
    agg = summarise([led.verify("a [E1]"), led.verify("no citation here")])
    assert agg["tasks"] == 2
    assert agg["citations"] == 1
    assert agg["tasks_with_no_citation"] == 1


# --- containment -----------------------------------------------------------

def test_unenforced_profile_passes_the_command_through(tmp_path):
    p = profile_for("none", tmp_path)
    assert wrap_argv(["echo", "hi"], p) == ["echo", "hi"]
    assert p.enforced is False


def test_enforced_profile_disables_network_and_is_rooted(tmp_path):
    p = profile_for("enforced", tmp_path)
    assert p.network is False
    assert p.enforced is True
    assert p.project_root == tmp_path.resolve()


@pytest.mark.skipif(sys.platform != "linux", reason="user namespaces are Linux-only")
def test_enforced_wrap_builds_a_bwrap_command(tmp_path):
    p = profile_for("enforced", tmp_path)
    ok, reason = available(p)
    if not ok:
        pytest.skip(reason)
    argv = wrap_argv(["bash", "-c", "ls"], p)
    assert argv[0].endswith("bwrap")
    assert "--unshare-net" in argv
    assert "--die-with-parent" in argv
    assert argv[-3:] == ["--", "bash", "-c"][:1] + ["bash", "-c"][:0] + ["bash", "-c", "ls"][-2:]


def test_enforcement_refuses_rather_than_degrading_silently(tmp_path, monkeypatch):
    """An arm labelled 'enforced' that quietly ran unconfined would invalidate
    the security results, so an unavailable profile must raise."""
    monkeypatch.setattr("agent.containment.bwrap_path", lambda: None)
    monkeypatch.setattr("agent.containment.sys", type("S", (), {"platform": "linux"}))
    p = profile_for("enforced", tmp_path)
    with pytest.raises(ContainmentUnavailable):
        wrap_argv(["ls"], p)
