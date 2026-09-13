"""Write tier — edit / write / multi_edit (Claude Code contracts).

These are the only tools with `check_permissions`, so the permission engine gates
exactly them (and nothing else). All mutations are read-before-write, freshness-
checked, atomic, reversible (backups), and return a unified diff. The Sandbox is the
hard wall underneath; these add the precise, fail-closed policy on top.
"""

import os

from .. import config, edits
from .base import Tool


def _backup_dir(sandbox):
    return os.path.join(str(sandbox.root), config.EDIT_BACKUP_DIRNAME)


def _guard(args, sandbox):
    """Tool-specific permission objection: deny if the path escapes the sandbox."""
    try:
        sandbox.resolve(args.get("path", ""))
    except Exception:
        return "deny"
    return "ask"


def _too_big(text):
    return len(text.encode("utf-8", "replace")) > config.EDIT_MAX_BYTES


def _edit(args, sandbox):
    path = args.get("path")
    old = args.get("old_string")
    new = args.get("new_string", "")
    if not path:
        return "ERROR: 'path' is required"
    if old is None:
        return "ERROR: 'old_string' is required"
    if old == new:
        return "ERROR: old_string and new_string are identical"
    p = sandbox.resolve(path)
    if not p.exists():
        return f"ERROR: no such file: {path}"
    current = p.read_text(encoding="utf-8", errors="replace")
    if edits.looks_binary(current):
        return f"ERROR: refusing to edit binary file: {path}"
    if not edits.was_read(p):
        return f"ERROR: read {path} before editing it."
    if not edits.is_fresh(p, current):
        return f"ERROR: {path} changed on disk since you read it — read it again before editing."
    count = current.count(old)
    if count == 0:
        return f"ERROR: old_string not found in {path}"
    replace_all = bool(args.get("replace_all"))
    if count > 1 and not replace_all:
        return (
            f"ERROR: old_string is not unique in {path} ({count} matches); "
            "add surrounding context to make it unique, or set replace_all."
        )
    updated = current.replace(old, new) if replace_all else current.replace(old, new, 1)
    if _too_big(updated):
        return f"ERROR: result exceeds the size cap ({config.EDIT_MAX_BYTES} bytes)."
    edits.atomic_write(p, updated, backup_dir=_backup_dir(sandbox))
    rel = str(sandbox.relativize(p)).replace("\\", "/")
    n = count if replace_all else 1
    return f"edited {rel} ({n} replacement{'s' if n != 1 else ''})\n{edits.unified_diff(current, updated, rel)}".rstrip()


def _write(args, sandbox):
    path = args.get("path")
    content = args.get("content")
    if not path:
        return "ERROR: 'path' is required"
    if content is None:
        return "ERROR: 'content' is required"
    if _too_big(content):
        return f"ERROR: content exceeds the size cap ({config.EDIT_MAX_BYTES} bytes)."
    p = sandbox.resolve(path)
    existed = p.exists()
    if existed:
        current = p.read_text(encoding="utf-8", errors="replace")
        if edits.looks_binary(current):
            return f"ERROR: refusing to overwrite binary file: {path}"
        if not edits.was_read(p) or not edits.is_fresh(p, current):
            return f"ERROR: {path} exists — read it first, and it must be unchanged since, before overwriting."
    else:
        p.parent.mkdir(parents=True, exist_ok=True)  # create parents WITHIN the sandbox
        current = ""
    edits.atomic_write(p, content, backup_dir=_backup_dir(sandbox))
    rel = str(sandbox.relativize(p)).replace("\\", "/")
    verb = "overwrote" if existed else "created"
    return f"{verb} {rel}\n{edits.unified_diff(current, content, rel)}".rstrip()


def _multi_edit(args, sandbox):
    path = args.get("path")
    editlist = args.get("edits")
    if not path:
        return "ERROR: 'path' is required"
    if not isinstance(editlist, list) or not editlist:
        return "ERROR: 'edits' must be a non-empty list of {old_string, new_string}."
    p = sandbox.resolve(path)
    if not p.exists():
        return f"ERROR: no such file: {path}"
    current = p.read_text(encoding="utf-8", errors="replace")
    if edits.looks_binary(current):
        return f"ERROR: refusing to edit binary file: {path}"
    if not edits.was_read(p) or not edits.is_fresh(p, current):
        return f"ERROR: read {path} before editing (and it must be unchanged since)."
    updated = current
    for i, e in enumerate(editlist, 1):
        old = e.get("old_string")
        new = e.get("new_string", "")
        if old is None:
            return f"ERROR: edit #{i} is missing 'old_string'"
        cnt = updated.count(old)
        if cnt == 0:
            return f"ERROR: edit #{i}: old_string not found"
        if cnt > 1 and not e.get("replace_all"):
            return f"ERROR: edit #{i}: old_string not unique ({cnt} matches); add context or set replace_all."
        updated = updated.replace(old, new) if e.get("replace_all") else updated.replace(old, new, 1)
    if updated == current:
        return "ERROR: the edits produced no change"
    if _too_big(updated):
        return f"ERROR: result exceeds the size cap ({config.EDIT_MAX_BYTES} bytes)."
    edits.atomic_write(p, updated, backup_dir=_backup_dir(sandbox))
    rel = str(sandbox.relativize(p)).replace("\\", "/")
    return f"applied {len(editlist)} edits to {rel}\n{edits.unified_diff(current, updated, rel)}".rstrip()


edit_tool = Tool(
    name="edit",
    description=(
        "Replace an exact string in a file. Args: path, old_string, new_string, replace_all? "
        "old_string must match EXACTLY ONCE (add surrounding context to disambiguate) unless "
        "replace_all is true. You must `read` the file first. Returns a unified diff."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "file to edit (relative to project root)"},
            "old_string": {"type": "string", "description": "exact text to replace (unique unless replace_all)"},
            "new_string": {"type": "string", "description": "replacement text"},
            "replace_all": {"type": "boolean", "description": "replace every occurrence (optional)"},
        },
        "required": ["path", "old_string", "new_string"],
    },
    run=_edit,
    read_only=False,
    check_permissions=_guard,
)

write_tool = Tool(
    name="write",
    description=(
        "Create a new file, or overwrite an existing one, with `content`. Overwriting requires "
        "you to have `read` the file first (and it must be unchanged since). Returns a diff."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "file path (relative to project root)"},
            "content": {"type": "string", "description": "full file contents to write"},
        },
        "required": ["path", "content"],
    },
    run=_write,
    read_only=False,
    check_permissions=_guard,
)

multi_edit_tool = Tool(
    name="multi_edit",
    description=(
        "Apply several edits to ONE file atomically (all-or-nothing). Args: path, edits (a list "
        "of {old_string, new_string, replace_all?}). Requires a prior `read`. Returns a diff."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "file to edit"},
            "edits": {
                "type": "array",
                "description": "list of {old_string, new_string, replace_all?} applied in order",
                "items": {"type": "object"},
            },
        },
        "required": ["path", "edits"],
    },
    run=_multi_edit,
    read_only=False,
    check_permissions=_guard,
)
