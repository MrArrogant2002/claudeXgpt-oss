"""Grep tool (M3) — middle step of the funnel: search file contents by regex.

Prefers ripgrep (`rg --json`) — fast and won't catastrophically backtrack. Falls
back to a pure-Python walk if `rg` isn't on PATH, so the agent still works without it.
Returns `path:line: text` matches.
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
}

from .base import Tool


def _grep_rg(args, sandbox):
    target = sandbox.resolve(args.get("path", "."))
    max_matches = int(args.get("max_matches", 100))
    cmd = ["rg", "--json", "--max-count", str(max_matches)]
    if args.get("glob"):
        cmd += ["--glob", args["glob"]]
    if args.get("ignore_case"):
        cmd += ["-i"]
    cmd += ["--", args["pattern"], str(target)]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    out = []
    for line in proc.stdout.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "match":
            continue
        d = obj["data"]
        path = d["path"]["text"]
        lineno = d["line_number"]
        text = d["lines"]["text"].rstrip("\n")
        try:
            rel = sandbox.relativize(path)
        except PermissionError:
            rel = path
        out.append(f"{rel}:{lineno}: {text}")
        if len(out) >= max_matches:
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
    files = [target] if target.is_file() else target.rglob("*")
    out = []
    for f in files:
        if not f.is_file():
            continue
        if any(part in _IGNORE_DIRS for part in f.parts):
            continue
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                for i, line in enumerate(fh, 1):
                    if rx.search(line):
                        out.append(f"{sandbox.relativize(f)}:{i}: {line.rstrip()}")
                        if len(out) >= max_matches:
                            return "\n".join(out)
        except (OSError, PermissionError):
            continue
    return "\n".join(out) if out else "(no matches)"


def _grep(args, sandbox):
    if shutil.which("rg"):
        return _grep_rg(args, sandbox)
    return _grep_py(args, sandbox)


grep_tool = Tool(
    name="grep",
    description=(
        "Search file contents with a regular expression. Returns matching lines "
        "as 'path:line: text'. Use to find where a symbol, function, or string lives."
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
