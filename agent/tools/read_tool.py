"""Read tool (M2) — deepest step of the funnel: pull specific file lines.

Supports a line range so the model can grab lines 40-90 instead of a whole
2000-line file. Returns numbered lines (so the model can cite / re-grep by line).

Robustness: models don't always use the exact param names, so we accept aliases
(start_line/line_start/start, end_line/line_end/end). If no end is given we return
a capped window (config.READ_DEFAULT_LINES) instead of the whole file, and tell the
model how to paginate — this prevents an accidental whole-file read from blowing the
context window.
"""

from .. import config
from .base import Tool


def _pick(args, *names):
    for n in names:
        v = args.get(n)
        if v is not None:
            return v
    return None


def _read(args, sandbox):
    p = sandbox.resolve(args["path"])
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    n = len(lines)

    start = int(_pick(args, "start_line", "line_start", "start") or 1)
    start = max(1, start)

    end_val = _pick(args, "end_line", "line_end", "end")
    if end_val is None:
        end = min(n, start + config.READ_DEFAULT_LINES - 1)  # capped default window
    else:
        end = min(int(end_val), n)

    if start > n:
        return f"(file has {n} lines; start_line {start} is past end of file)"

    numbered = [f"{i:>6}\t{lines[i - 1]}" for i in range(start, end + 1)]
    header = f"# {sandbox.relativize(p)}  (lines {start}-{end} of {n})\n"
    body = "\n".join(numbered) if numbered else "(empty range)"
    more = (
        ""
        if end >= n
        else f"\n\n… {n - end} more lines. Call read again with start_line={end + 1} to continue."
    )
    return header + body + more


read_tool = Tool(
    name="read",
    description=(
        "Read a file's contents, optionally a line range (start_line, end_line, both "
        "1-indexed). Returns numbered lines. If you omit end_line it returns a capped "
        "window and tells you how to page to the next chunk. Use after glob/grep locate "
        "the file worth reading."
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
                "description": "1-indexed start line (optional, default 1)",
            },
            "end_line": {
                "type": "integer",
                "description": "1-indexed end line (optional; default = start + a capped window)",
            },
        },
        "required": ["path"],
    },
    run=_read,
    read_only=True,
)
