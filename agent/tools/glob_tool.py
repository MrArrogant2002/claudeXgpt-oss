"""Glob tool (M3) — broadest step of the funnel: locate files by path pattern.
Does not read contents. Cheapest way to narrow the search space."""

from .base import Tool


def _glob(args, sandbox):
    pattern = args["pattern"]
    limit = min(int(args.get("limit", 200)), 1000)
    try:
        it = sandbox.root.glob(pattern)
    except (NotImplementedError, ValueError) as e:
        return f"ERROR: bad glob pattern {pattern!r}: {e}"

    hits = []
    for p in it:
        try:
            if p.is_file():
                rel = sandbox.relativize(p)
                hits.append(str(rel).replace("\\", "/"))
                if len(hits) >= limit:
                    break
        except (PermissionError, OSError):
            continue
    return "\n".join(sorted(hits)) if hits else "(no matches)"


glob_tool = Tool(
    name="glob",
    description=(
        "Find files by glob pattern relative to the project root (e.g. '**/*.py', "
        "'src/**/*config*'). Returns matching file paths only — does not read them."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern, e.g. **/*.py"},
            "limit": {
                "type": "integer",
                "description": "Max paths to return (default 200)",
            },
        },
        "required": ["pattern"],
    },
    run=_glob,
    read_only=True,
)
