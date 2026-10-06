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
    rel = _pick(args, "path", "file_path", "filename")
    if not rel:
        return "ERROR: read requires a 'path'"
    try:
        p = sandbox.resolve(rel)
    except (PermissionError, OSError, ValueError) as e:
        # A path outside the project is something the model can act on, not a
        # crash: it should try another path rather than lose the turn.
        return f"ERROR: {e}"
    if not p.exists():
        return f"(no such file: {rel})"
    if p.is_dir():
        return f"ERROR: {rel} is a directory — use list_dir to see its entries, or read a file inside it."

    from .. import edits

    # Guard before reading: the tool returns a window of a few hundred lines, but
    # read_text pulls the whole file into memory first. A committed dataset or a
    # minified bundle would otherwise exhaust RAM to show 300 lines.
    try:
        size = p.stat().st_size
    except OSError as e:
        return f"ERROR: cannot stat {rel}: {e}"
    if size > config.READ_MAX_BYTES:
        return (
            f"ERROR: {rel} is {size} bytes, over the {config.READ_MAX_BYTES}-byte "
            "read limit. Use grep to find the relevant lines, or raise "
            "AGENT_READ_MAX_BYTES."
        )

    text = p.read_text(encoding="utf-8", errors="replace")
    if edits.looks_binary(text):
        size = p.stat().st_size
        return (
            f"(binary file: {sandbox.relativize(p)}, {size} bytes — not shown. "
            "Reading it as text would be meaningless; use grep for embedded strings if needed.)"
        )
    # Record the full-file hash so the write tools can enforce read-before-write /
    # freshness. No effect on what read returns; harmless when editing is disabled.
    try:
        edits.record_read(p, text)
    except Exception:
        pass
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
