"""Read tool (M2) — deepest step of the funnel: pull specific file lines.

Supports a line range so the model can grab lines 40-90 instead of a whole
2000-line file. Returns numbered lines (so the model can cite / re-grep by line).
"""

from .base import Tool


def _read(args, sandbox):
    p = sandbox.resolve(args["path"])
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    n = len(lines)
    start = max(1, int(args.get("start_line", 1)))
    end = int(args.get("end_line", n))
    end = min(end, n)
    if start > n:
        return f"(file has {n} lines; start_line {start} is past end of file)"
    numbered = [f"{i:>6}\t{lines[i - 1]}" for i in range(start, end + 1)]
    header = f"# {sandbox.relativize(p)}  (lines {start}-{end} of {n})\n"
    return header + ("\n".join(numbered) if numbered else "(empty range)")


read_tool = Tool(
    name="read",
    description=(
        "Read a file's contents, optionally a line range. Returns numbered lines. "
        "Use after glob/grep have located the file worth reading."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to the project root",
            },
            "start_line": {
                "type": "integer",
                "description": "1-indexed start line (optional)",
            },
            "end_line": {
                "type": "integer",
                "description": "1-indexed end line (optional)",
            },
        },
        "required": ["path"],
    },
    run=_read,
    read_only=True,
)
