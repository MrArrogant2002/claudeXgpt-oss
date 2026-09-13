"""File-mutation infrastructure for the write tools — stdlib only, fully local.

Provides the read-before-write freshness registry (so an edit can't blindly clobber
a file the model never read, or one that changed since it read it), atomic writes
(temp file -> fsync -> os.replace), reversible backups, and unified-diff rendering.
"""

import difflib
import hashlib
import os
import tempfile
import time
from pathlib import Path

# abs-path -> sha256 of the full file content when it was last read (or written).
# Populated by read_tool on every read; checked by edit/write to enforce freshness.
_READ_STATE = {}


def _key(path):
    return str(Path(path).resolve())


def _hash(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def record_read(path, text):
    """Remember the content hash of a file the agent has read (read-before-write)."""
    _READ_STATE[_key(path)] = _hash(text)


def was_read(path):
    return _key(path) in _READ_STATE


def is_fresh(path, current_text):
    """True if `path` was read and hasn't changed on disk since (no external edit)."""
    k = _key(path)
    return k in _READ_STATE and _READ_STATE[k] == _hash(current_text)


def looks_binary(text):
    return "\x00" in text


def atomic_write(path, content, backup_dir=None):
    """Write `content` to `path` atomically. Backs up prior bytes first (reversible),
    then temp-write + fsync + os.replace so an interrupt never leaves a partial file.
    Returns the prior text (or None if the file is new). Refreshes the read-state."""
    p = Path(path)
    prior = None
    if p.exists():
        prior = p.read_text(encoding="utf-8", errors="replace")
        if backup_dir:
            bd = Path(backup_dir)
            bd.mkdir(parents=True, exist_ok=True)
            stamp = int(time.time() * 1000)
            (bd / f"{p.name}.{stamp}.bak").write_text(prior, encoding="utf-8", errors="replace")

    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".agent-tmp-", suffix=p.suffix or ".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(p))
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass

    record_read(path, content)  # the file now matches what we wrote -> stays "fresh"
    return prior


def unified_diff(old, new, rel_path):
    """Compact unified diff for showing what changed (empty string if identical)."""
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{rel_path}",
        tofile=f"b/{rel_path}",
        n=3,
    )
    return "".join(diff)
