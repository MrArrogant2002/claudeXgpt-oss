"""Core agent behaviour: protocol parsing, the turn loop, and the tool funnel.

No model server is needed; the inference client is replaced where a turn has to
run. These cover the paths that decide whether a session survives a bad
completion, which is most of what "robust" means for this agent.
"""

import pytest

from agent import config, context, harmony_codec as hc, loop
from agent.sandbox import Sandbox
from agent.tools import default_registry


@pytest.fixture
def project(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text(
        "\n".join(f"line {i}" for i in range(1, 501)) + "\n", encoding="utf-8"
    )
    (tmp_path / "pkg" / "util.py").write_text("def helper():\n    return 42\n",
                                              encoding="utf-8")
    (tmp_path / "notes.md").write_text("# notes\nhelper lives in util\n",
                                       encoding="utf-8")
    return tmp_path


def enc():
    return hc.encoding()


def E(text):
    return hc.encoding().encode(text, allowed_special="all")


# --- protocol --------------------------------------------------------------

def test_well_formed_tool_call_parses():
    msgs = hc.parse(E('<|channel|>commentary to=functions.read <|constrain|>json'
                      '<|message|>{"path":"a.py"}<|call|>'))
    f = hc.msg_fields(msgs[-1])
    assert f["recipient"] == "functions.read"
    assert f["content"] == '{"path":"a.py"}'


def test_duplicated_recipient_is_salvaged_not_fatal():
    """The malformation actually observed from gpt-oss. The strict parser
    rejects it whole; losing the turn over a repeated token would be worse."""
    before = hc.salvage_count()
    msgs = hc.parse(E('<|channel|>commentary to=functions.read to=functions.read '
                      '<|constrain|>json<|message|>{"path":"a.py"}<|call|>'))
    f = hc.msg_fields(msgs[-1])
    assert f["recipient"] == "functions.read"
    assert hc.salvage_count() > before


def test_bare_text_with_no_markers_is_salvaged_as_the_answer():
    msgs = hc.parse(E("the answer is 42"))
    assert any(hc.msg_fields(m)["content"].strip() == "the answer is 42" for m in msgs)


def test_stale_reasoning_is_dropped_between_turns():
    history = [
        hc.user_message("q"),
        hc.Message.from_role_and_content(hc.Role.ASSISTANT, "thinking").with_channel("analysis"),
        hc.Message.from_role_and_content(hc.Role.ASSISTANT, "answer").with_channel("final"),
    ]
    kept = context.drop_stale_cot(history)
    assert all(hc.msg_fields(m)["channel"] != "analysis" for m in kept)
    assert len(kept) == 2


def test_oversized_tool_results_are_truncated():
    out = context.budget("x" * 100_000, cap=500)
    assert len(out) < 1000 and "truncated" in out


# --- tools -----------------------------------------------------------------

def test_read_paginates_instead_of_dumping_a_whole_file(project):
    out = default_registry().get("read").run({"path": "pkg/core.py"}, Sandbox(project))
    assert "lines 1-300 of 500" in out
    assert "start_line=301" in out, "the model must be told how to continue"


def test_read_honours_an_explicit_range(project):
    out = default_registry().get("read").run(
        {"path": "pkg/core.py", "start_line": 10, "end_line": 12}, Sandbox(project))
    assert "line 10" in out and "line 12" in out and "line 13" not in out


def test_read_accepts_the_aliases_models_actually_use(project):
    out = default_registry().get("read").run(
        {"file_path": "pkg/util.py", "start": 1, "end": 2}, Sandbox(project))
    assert "def helper" in out


def test_read_refuses_a_file_over_the_size_limit(project, monkeypatch):
    monkeypatch.setattr(config, "READ_MAX_BYTES", 100)
    out = default_registry().get("read").run({"path": "pkg/core.py"}, Sandbox(project))
    assert "over the" in out and "read limit" in out


def test_read_reports_a_directory_clearly(project):
    out = default_registry().get("read").run({"path": "pkg"}, Sandbox(project))
    assert "directory" in out and "list_dir" in out


def test_grep_finds_a_symbol_with_its_location(project):
    out = default_registry().get("grep").run({"pattern": "def helper"}, Sandbox(project))
    assert "pkg/util.py:1:" in out


def test_grep_accepts_the_query_alias(project):
    out = default_registry().get("grep").run({"query": "helper"}, Sandbox(project))
    assert "util.py" in out


def test_grep_reports_no_matches_rather_than_failing(project):
    out = default_registry().get("grep").run(
        {"pattern": "zzz_not_present"}, Sandbox(project))
    assert out == "(no matches)"


def test_grep_rejects_a_bad_regex_as_data(project):
    out = default_registry().get("grep").run({"pattern": "("}, Sandbox(project))
    assert "ERROR" in out


def test_glob_finds_files_and_skips_noise(project):
    (project / ".venv").mkdir()
    (project / ".venv" / "junk.py").write_text("x\n", encoding="utf-8")
    out = default_registry().get("glob").run({"pattern": "**/*.py"}, Sandbox(project))
    assert "pkg/util.py" in out and ".venv" not in out


def test_list_dir_marks_directories(project):
    out = default_registry().get("list_dir").run({"path": "."}, Sandbox(project))
    assert "pkg/" in out and "notes.md" in out


# --- the turn loop ---------------------------------------------------------

class FakeModel:
    """Replays completions in order, so a turn can run without a server."""

    def __init__(self, *completions):
        self.completions = [E(c) for c in completions]
        self.calls = 0

    def __call__(self, prefill_ids, stop_ids=None, max_tokens=None, **kw):
        if self.calls >= len(self.completions):
            raise AssertionError("the loop asked for more completions than planned")
        out = self.completions[self.calls]
        self.calls += 1
        return out, {}


@pytest.fixture
def fake(monkeypatch):
    def install(*completions):
        model = FakeModel(*completions)
        monkeypatch.setattr("agent.inference.complete", model)
        return model
    return install


def test_a_tool_call_runs_and_its_result_reaches_the_model(project, fake):
    fake('<|channel|>commentary to=functions.read <|constrain|>json'
         '<|message|>{"path":"pkg/util.py"}<|call|>',
         "<|channel|>final<|message|>It returns 42.<|return|>")
    events = []
    result, _ = loop.run_turn("what does helper do?", [], default_registry(),
                              Sandbox(project), on_event=events.append)
    assert result.reason == "completed" and "42" in result.answer
    tool_out = [e for e in events if e.get("role") == "tool"]
    assert "def helper" in tool_out[0]["content"]


def test_an_unknown_tool_is_reported_as_data_not_a_crash(project, fake):
    fake('<|channel|>commentary to=functions.nosuchtool <|constrain|>json'
         '<|message|>{}<|call|>',
         "<|channel|>final<|message|>recovered<|return|>")
    result, _ = loop.run_turn("q", [], default_registry(), Sandbox(project))
    assert result.reason == "completed"


def test_invalid_json_arguments_are_reported_as_data(project, fake):
    fake('<|channel|>commentary to=functions.read <|constrain|>json'
         '<|message|>{not json<|call|>',
         "<|channel|>final<|message|>recovered<|return|>")
    result, _ = loop.run_turn("q", [], default_registry(), Sandbox(project))
    assert result.reason == "completed"


def test_a_failing_tool_does_not_end_the_turn(project, fake):
    fake('<|channel|>commentary to=functions.read <|constrain|>json'
         '<|message|>{"path":"does/not/exist.py"}<|call|>',
         "<|channel|>final<|message|>that file is missing<|return|>")
    result, _ = loop.run_turn("q", [], default_registry(), Sandbox(project))
    assert result.reason == "completed" and "missing" in result.answer


def test_reasoning_is_never_returned_as_the_answer(project, fake):
    """A last-resort path once returned the longest analysis message as the
    answer, showing the user private reasoning the model declined to commit to."""
    analysis_only = "<|channel|>analysis<|message|>I am not sure yet<|end|>"
    fake(*([analysis_only] * 8))
    result, _ = loop.run_turn("q", [], default_registry(), Sandbox(project),
                              max_turns=3)
    assert result.reason in ("no_answer", "max_turns")
    assert "I am not sure yet" not in (result.answer or "")


def test_the_loop_stops_at_the_turn_limit(project, fake):
    call = ('<|channel|>commentary to=functions.read <|constrain|>json'
            '<|message|>{"path":"pkg/util.py"}<|call|>')
    fake(*([call] * 10))
    result, _ = loop.run_turn("q", [], default_registry(), Sandbox(project),
                              max_turns=3)
    assert result.turns <= 3


def test_a_model_error_is_returned_not_raised(project, monkeypatch):
    from agent import inference

    def boom(*a, **k):
        raise inference.InferenceError("server down")

    monkeypatch.setattr("agent.inference.complete", boom)
    result, _ = loop.run_turn("q", [], default_registry(), Sandbox(project))
    assert result.reason == "model_error" and "server down" in result.answer


def test_bash_is_absent_from_the_registry_by_default():
    assert default_registry(allow_exec=False).get("bash") is None
    assert default_registry(allow_exec=True).get("bash") is not None


def test_write_tools_are_absent_by_default():
    reg = default_registry(allow_edit=False)
    assert all(reg.get(n) is None for n in ("edit", "write", "multi_edit"))
