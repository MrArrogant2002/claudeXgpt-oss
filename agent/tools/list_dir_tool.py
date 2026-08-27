"""list_dir tool — cheap orientation: list the entries of a directory (dirs first,
with a trailing slash). Useful for getting the lay of the land before globbing."""

from .base import Tool

_SKIP = {".git", ".svn", ".hg", "__pycache__", ".venv", "node_modules"}


def _list_dir(args, sandbox):
    rel = args.get("path", ".") or "."
    p = sandbox.resolve(rel)
    if not p.exists():
        return f"(no such path: {rel})"
    if not p.is_dir():
        return f"ERROR: not a directory: {rel}"
    dirs, files = [], []
    try:
        for child in p.iterdir():
            if child.name in _SKIP:
                continue
            if child.is_dir():
                dirs.append(child.name + "/")
            else:
                files.append(child.name)
    except OSError as e:
        return f"ERROR: {e}"
    entries = sorted(dirs) + sorted(files)
    header = f"# {rel.rstrip('/')}/  ({len(dirs)} dirs, {len(files)} files)\n"
    return header + ("\n".join(entries) if entries else "(empty)")


list_dir_tool = Tool(
    name="list_dir",
    description=(
        "List the entries of a directory (subdirectories first, marked with a trailing '/'). "
        "Path is relative to the project root; defaults to '.'. Use to orient yourself before "
        "globbing/grepping."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path relative to project root (default '.')",
            },
        },
        "required": [],
    },
    run=_list_dir,
    read_only=True,
)
