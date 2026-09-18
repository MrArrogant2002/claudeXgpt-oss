"""Glob tool (M3) — broadest step of the funnel: locate files by path pattern.
Does not read contents. Cheapest way to narrow the search space.

Accuracy upgrades (match Claude Code's Glob behaviour):
  * skips dependency / VCS / cache directories so a `**/*.py` in a repo with a
    `.venv` or `node_modules` doesn't drown the real matches in thousands of hits;
  * returns matches MOST-RECENTLY-MODIFIED first, so the files you're likely
    working on surface at the top instead of being sorted alphabetically;
  * accepts a `path` to root the search at a subdirectory, and tolerates the
    `glob` alias for `pattern` (models don't always use the exact name).
"""

from .base import Tool

# Directories whose contents are almost never what a code question is about.
# Kept in sync with grep/list_dir/lsp so the funnel behaves consistently.
_IGNORE_DIRS = {
    ".git", ".svn", ".hg", ".bzr", ".jj", ".sl",
    "node_modules", ".venv", "venv", "env", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    "dist", "build", "target", ".agent-backups", ".idea", ".vscode",
}


def _ignored(rel_parts):
    return any(part in _IGNORE_DIRS for part in rel_parts)


def _glob(args, sandbox):
    pattern = args.get("pattern") or args.get("glob")
    if not pattern:
        return "ERROR: glob requires a 'pattern' (e.g. '**/*.py')"
    limit = min(int(args.get("limit", 200)), 1000)

    base = sandbox.root
    subdir = args.get("path")
    if subdir and subdir not in (".", "./"):
        try:
            base = sandbox.resolve(subdir)
        except (PermissionError, OSError) as e:
            return f"ERROR: bad path {subdir!r}: {e}"
        if not base.is_dir():
            return f"ERROR: not a directory: {subdir}"

    try:
        it = base.glob(pattern)
    except (NotImplementedError, ValueError) as e:
        return f"ERROR: bad glob pattern {pattern!r}: {e}"

    hits = []
    for p in it:
        try:
            if not p.is_file():
                continue
            rel = sandbox.relativize(p)
            if _ignored(rel.parts):
                continue
            hits.append((p.stat().st_mtime, str(rel).replace("\\", "/")))
        except (PermissionError, OSError):
            continue

    if not hits:
        return "(no matches)"
    hits.sort(key=lambda t: (-t[0], t[1]))  # newest first, then stable by path
    paths = [h[1] for h in hits[:limit]]
    out = "\n".join(paths)
    if len(hits) > limit:
        out += f"\n… {len(hits) - limit} more match(es); raise `limit` or narrow the pattern."
    return out


glob_tool = Tool(
    name="glob",
    description=(
        "Find files by glob pattern relative to the project root (e.g. '**/*.py', "
        "'src/**/*config*'). Returns matching file paths only — does not read them — "
        "most-recently-modified first, skipping VCS/dependency/build dirs. Optional "
        "`path` roots the search at a subdirectory."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern, e.g. **/*.py"},
            "path": {
                "type": "string",
                "description": "Subdirectory to search within (optional, default project root)",
            },
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
