"""Permission engine (Claude Code–style) — the spine of the write tier.

Every *managed* (mutating) tool call passes through `resolve()` before it runs.
Resolution order, first decision wins:

  0. tool not managed (no check_permissions) -> allow  (bash & read-only tools untouched)
  1. always-deny sensitive paths (.env, .git, keys, vendor, .venv…) — even under bypass
  2. configured deny rule / session-deny            (deny always wins)
  3. tool.check_permissions(args, sandbox) == deny   (e.g. path escapes the sandbox)
  4. allow rule / session-allow                       -> allow
  5. mode policy: bypassPermissions|dontAsk->allow · plan->deny · acceptEdits->allow · default->ask
  6. "ask": call the interactive prompter if present, else DENY (fail-closed)

Pure local logic — no network. The Sandbox is still the hard wall underneath; this is
policy on top of it. Default posture is fail-closed: mode `plan` (read-only) unless changed.
"""

import re
from dataclasses import dataclass

ALLOW, ASK, DENY = "allow", "ask", "deny"
MODES = ("plan", "default", "acceptEdits", "bypassPermissions", "dontAsk")

# Always-deny (belt-and-suspenders; applies in every mode, even bypassPermissions).
_SENSITIVE_SEGMENTS = {
    ".git", ".hg", ".svn", ".ssh", ".aws", ".gnupg",
    ".venv", "venv", "node_modules", "vendor", "__pycache__",
}
_SENSITIVE_NAMES = {".env"}
_SENSITIVE_SUFFIXES = (".pem", ".key", ".pfx", ".p12")
_SENSITIVE_PREFIXES = ("id_rsa", "id_ed25519", ".env", "secret", "credentials")

_RULE_RE = re.compile(r"^\s*(\w+)\s*(?:\((.*)\))?\s*$")


@dataclass
class Decision:
    behavior: str  # "allow" | "deny"
    reason: str = ""


def _rel_posix(path, sandbox):
    """Sandbox-relative POSIX path (or None if it escapes the root)."""
    try:
        return str(sandbox.relativize(sandbox.resolve(path))).replace("\\", "/")
    except Exception:
        return None


def _is_sensitive(rel):
    parts = rel.split("/")
    if any(seg in _SENSITIVE_SEGMENTS for seg in parts):
        return True
    base = parts[-1].lower()
    if base in _SENSITIVE_NAMES or base.endswith(_SENSITIVE_SUFFIXES):
        return True
    return any(base.startswith(p) for p in _SENSITIVE_PREFIXES)


def _parse_rule(rule):
    """'Edit(src/**)' -> ('edit', 'src/**'); 'Write' -> ('write', None)."""
    m = _RULE_RE.match(rule or "")
    if not m:
        return None, None
    return m.group(1).lower(), m.group(2)


def _rule_matches(rules, tool_name, rel):
    import fnmatch

    for rule in rules or []:
        rt, pat = _parse_rule(rule)
        if rt is None or rt != tool_name:
            continue
        if pat is None or (rel is not None and fnmatch.fnmatch(rel, pat)):
            return True
    return False


class PermissionEngine:
    """Holds the mode + rules + optional interactive prompter and resolves decisions.
    `prompter(tool_name, args, spec) -> 'allow_once'|'allow_session'|'always'|'deny'`."""

    def __init__(self, mode="plan", rules=None, prompter=None):
        self.mode = mode if mode in MODES else "plan"
        self.rules = rules or {}
        self.prompter = prompter
        self._session_allow = set()
        self._session_deny = set()

    # The callback passed to loop.run_turn(can_use_tool=...).
    def can_use_tool(self, tool, args, sandbox) -> Decision:
        # 0. Only tools that opt in (edit/write/multi_edit) are managed; bash and
        #    read-only tools carry no check_permissions and are left untouched.
        if getattr(tool, "check_permissions", None) is None:
            return Decision(ALLOW)

        rel = _rel_posix(args.get("path", ""), sandbox)
        spec = f"{tool.name}:{rel if rel is not None else args.get('path', '')}"

        # 1. always-deny sensitive paths
        if rel is not None and _is_sensitive(rel):
            return Decision(DENY, f"'{rel}' is a protected path (secrets/vcs/vendored)")
        # 2. configured deny / session deny (deny wins)
        if spec in self._session_deny or _rule_matches(self.rules.get("deny"), tool.name, rel):
            return Decision(DENY, "blocked by a deny rule")
        # 3. tool-specific objection (e.g. path escapes the sandbox)
        try:
            objection = tool.check_permissions(args, sandbox)
        except Exception as e:
            return Decision(DENY, f"permission check failed: {e}")
        if objection == DENY:
            return Decision(DENY, "outside the project sandbox")
        # 4. explicit allow
        if spec in self._session_allow or _rule_matches(self.rules.get("allow"), tool.name, rel):
            return Decision(ALLOW)
        # 5. mode policy
        if self.mode in ("bypassPermissions", "dontAsk"):
            return Decision(ALLOW)
        if self.mode == "plan":
            return Decision(DENY, "plan mode is read-only (use --permission-mode acceptEdits to allow edits)")
        if self.mode == "acceptEdits":
            return Decision(ALLOW)
        # 6. default mode -> ask; prompt if we can, else fail-closed
        if self.prompter is None:
            return Decision(DENY, "requires approval; run with --permission-mode acceptEdits or add an allow rule")
        answer = self.prompter(tool.name, args, spec)
        if answer == "deny":
            self._session_deny.add(spec)
            return Decision(DENY, "denied by user")
        if answer in ("allow_session", "always"):
            self._session_allow.add(spec)  # (persisting 'always' to settings is a later step)
        return Decision(ALLOW)
