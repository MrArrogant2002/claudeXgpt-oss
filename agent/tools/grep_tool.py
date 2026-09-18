"""Grep tool (M3) — middle step of the funnel: search file contents by regex.

Prefers ripgrep (`rg --json`) — fast and won't catastrophically backtrack. Falls
back to a pure-Python walk if `rg` isn't on PATH, so the agent still works without it.
Returns `path:line: text` for matches, and `path-line- text` for surrounding context
lines (GNU-grep style) when `context` > 0 — seeing a few lines around a hit lets the
model ground its answer without a second `read`.
"""

import json
import re
import shutil
import subprocess

_IGNORE_DIRS = {
    ".git",
    ".svn",
    ".hg",
    ".bzr",
    ".jj",
    ".sl",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".agent-backups",
}

from .base import Tool


def _rel(sandbox, path):
    try:
        return str(sandbox.relativize(path)).replace("\\", "/")
    except (PermissionError, ValueError, OSError):
        return str(path).replace("\\", "/")


def _grep_rg(args, sandbox):
    target = sandbox.resolve(args.get("path", "."))
    max_matches = int(args.get("max_matches", 100))
    context = max(0, min(int(args.get("context", 0)), 10))
    cmd = ["rg", "--json"]
    if context:
        cmd += ["-C", str(context)]
    if args.get("glob"):
        cmd += ["--glob", args["glob"]]
    if args.get("ignore_case"):
        cmd += ["-i"]
    cmd += ["--", args["pattern"], str(target)]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    out = []
    matched = 0  # cap counts MATCH lines only, not context lines
    for line in proc.stdout.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        typ = obj.get("type")
        if typ not in ("match", "context"):
            continue
        d = obj["data"]
        rel = _rel(sandbox, d["path"]["text"])
        lineno = d["line_number"]
        text = d["lines"]["text"].rstrip("\n")
        sep = ":" if typ == "match" else "-"
        out.append(f"{rel}{sep}{lineno}{sep} {text}")
        if typ == "match":
            matched += 1
            if matched >= max_matches:
                break
    return "\n".join(out) if out else "(no matches)"


def _grep_py(args, sandbox):
    flags = re.IGNORECASE if args.get("ignore_case") else 0
    try:
        rx = re.compile(args["pattern"], flags)
    except re.error as e:
        return f"ERROR: bad regex: {e}"
    target = sandbox.resolve(args.get("path", "."))
    max_matches = int(args.get("max_matches", 100))
    context = max(0, min(int(args.get("context", 0)), 10))
    glob_pat = args.get("glob")
    files = [target] if target.is_file() else target.rglob("*")
    out = []
    matched = 0
    for f in files:
        if not f.is_file():
            continue
        if any(part in _IGNORE_DIRS for part in f.parts):
            continue
        if glob_pat and not f.match(glob_pat):
            continue
        try:
            rel = _rel(sandbox, f)
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                lines = fh.read().splitlines()
        except (OSError, PermissionError):
            continue
        printed = set()  # avoid double-printing a line shared by overlapping windows
        for i, line in enumerate(lines):
            if not rx.search(line):
                continue
            lo = max(0, i - context)
            hi = min(len(lines), i + context + 1)
            for j in range(lo, hi):
                if j in printed:
                    continue
                printed.add(j)
                sep = ":" if j == i else "-"
                out.append(f"{rel}{sep}{j + 1}{sep} {lines[j].rstrip()}")
            matched += 1
            if matched >= max_matches:
                return "\n".join(out)
    return "\n".join(out) if out else "(no matches)"


def _grep(args, sandbox):
    pattern = args.get("pattern") or args.get("query")  # tolerate the `query` alias
    if not pattern:
        return "ERROR: grep requires a 'pattern' (the regex to search for)"
    args = {**args, "pattern": pattern}
    if shutil.which("rg"):
        return _grep_rg(args, sandbox)
    return _grep_py(args, sandbox)


grep_tool = Tool(
    name="grep",
    description=(
        "Search file contents with a regular expression. Returns matching lines "
        "as 'path:line: text'. Use to find where a symbol, function, or string lives. "
        "Set `context` (0-10) to also return that many lines around each match "
        "('path-line- text') so you can read a hit in place. `glob` restricts to "
        "matching files; `ignore_case` for case-insensitive."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex to search for"},
            "path": {
                "type": "string",
                "description": "Dir or file to search (default: project root)",
            },
            "glob": {
                "type": "string",
                "description": "Restrict to files matching this glob (optional)",
            },
            "ignore_case": {
                "type": "boolean",
                "description": "Case-insensitive search (optional)",
            },
            "context": {
                "type": "integer",
                "description": "Lines of context to show around each match, 0-10 (optional)",
            },
            "max_matches": {
                "type": "integer",
                "description": "Max matches to return (default 100)",
            },
        },
        "required": ["pattern"],
    },
    run=_grep,
    read_only=True,
)
