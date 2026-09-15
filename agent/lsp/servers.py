"""Language detection and language-server command resolution.

Everything is local: a server is used only if its binary is already installed on
this machine (same philosophy as ripgrep). If nothing is installed, the `lsp` tool
simply isn't offered and the agent behaves exactly as before (grep/read).
"""

import importlib.util
import os
import shutil
import sys
from collections import Counter

# language -> (file extensions, candidate commands in preference order)
LANGUAGE_SERVERS = {
    "python": ({".py", ".pyi"}, [["pyright-langserver", "--stdio"], [sys.executable, "-m", "pylsp"]]),
    "typescript": ({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}, [["typescript-language-server", "--stdio"]]),
    "go": ({".go"}, [["gopls"]]),
    "rust": ({".rs"}, [["rust-analyzer"]]),
    "c": ({".c", ".h", ".cc", ".cpp", ".hpp", ".hh", ".cxx"}, [["clangd"]]),
    "java": ({".java"}, [["jdtls"]]),
}

_EXT_TO_LANG = {ext: lang for lang, (exts, _) in LANGUAGE_SERVERS.items() for ext in exts}
_SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "__pycache__",
    "target", "dist", "build", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".agent-backups",
}


def _cmd_available(cmd):
    exe = cmd[0]
    if exe == sys.executable and len(cmd) >= 3 and cmd[1] == "-m":
        return importlib.util.find_spec(cmd[2]) is not None  # e.g. python -m pylsp
    return shutil.which(exe) is not None


def resolve_command(language):
    """First installed server command for `language`, or None."""
    entry = LANGUAGE_SERVERS.get(language)
    if not entry:
        return None
    for cmd in entry[1]:
        if _cmd_available(cmd):
            return cmd
    return None


def detect_language(path):
    _, ext = os.path.splitext(path or "")
    return _EXT_TO_LANG.get(ext.lower())


def any_available():
    """True if a server for at least one language is installed (gates the tool)."""
    return any(resolve_command(lang) for lang in LANGUAGE_SERVERS)


def available_languages():
    return sorted(lang for lang in LANGUAGE_SERVERS if resolve_command(lang))


def dominant_language(root):
    """The most common known-language extension under `root` that also has a server
    installed — used when the model names a symbol without a file."""
    counts = Counter()
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            lang = detect_language(fn)
            if lang:
                counts[lang] += 1
        scanned += len(filenames)
        if scanned > 4000:  # sample; don't walk giant monorepos fully
            break
    for lang, _ in counts.most_common():
        if resolve_command(lang):
            return lang
    return None
