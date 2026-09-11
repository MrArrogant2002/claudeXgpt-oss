"""lsp tool — semantic code intelligence via a local language server.

Resolves a symbol *by name* to precise locations (definition, references, type/
signature, file outline) instead of guessing from grep text matches. Returns
`path:line: label` lines — the same shape as grep — so the loop and UI need no
changes. Fully local: the server is a process on this box.

Cross-server strategy: to find where a symbol lives, we try `workspace/symbol`
(supported by pyright/gopls/rust-analyzer/clangd) and fall back to grepping the
identifier (needed for e.g. python-lsp-server, which lacks workspace/symbol). From
that seed position we call the server's `definition`/`references`/`hover`, so the
server still does the real resolution (following imports, inheritance, types). If no
server is installed for the project's language, we say so and the model uses grep.
"""

import atexit
import json
import os
import re
import shutil
import subprocess
import threading

from ..lsp import servers
from ..lsp.client import LspError, LspClient, path_to_uri, uri_to_path
from .base import Tool

_CLIENTS = {}  # (root, language) -> LspClient — kept warm across the session
_CLIENTS_LOCK = threading.Lock()
_MAX_RESULTS = 40

# LSP SymbolKind -> readable label
_KIND = {
    5: "class", 6: "method", 7: "property", 8: "field", 9: "constructor",
    10: "enum", 11: "interface", 12: "function", 13: "variable", 14: "constant",
    23: "struct", 24: "event", 25: "operator", 26: "type-param",
}
_OUTLINE_KINDS = {5, 6, 9, 10, 11, 12, 23}  # classes/methods/functions/… (skip import noise)


def _shutdown_all():
    with _CLIENTS_LOCK:
        for c in _CLIENTS.values():
            try:
                c.shutdown()
            except Exception:
                pass
        _CLIENTS.clear()


atexit.register(_shutdown_all)


def _client_for(root, language):
    cmd = servers.resolve_command(language)
    if not cmd:
        return None
    key = (root, language)
    with _CLIENTS_LOCK:
        c = _CLIENTS.get(key)
        if c is not None and c.alive():
            return c
        try:
            c = LspClient(cmd, root, language)
        except LspError:
            return None
        _CLIENTS[key] = c
        return c


# --- location extraction (Location / LocationLink) --------------------------
def _as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _loc_pos(loc):
    """(uri, line, character) from a Location or LocationLink, else (None, 0, 0)."""
    if not isinstance(loc, dict):
        return None, 0, 0
    if "uri" in loc and "range" in loc:
        s = loc["range"]["start"]
        return loc["uri"], s.get("line", 0), s.get("character", 0)
    if "targetUri" in loc:
        r = loc.get("targetSelectionRange") or loc.get("targetRange") or {}
        s = r.get("start") or {}
        return loc["targetUri"], s.get("line", 0), s.get("character", 0)
    return None, 0, 0


def _rel(sandbox, uri):
    path = uri_to_path(uri)
    try:
        return str(sandbox.relativize(path)).replace("\\", "/")
    except Exception:
        return path


def _fmt(sandbox, uri, line, label=""):
    return f"{_rel(sandbox, uri)}:{int(line) + 1}: {label}".rstrip()


def _hover_text(hover):
    if not hover:
        return ""
    parts = []
    for item in _as_list(hover.get("contents")):
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            parts.append(item.get("value", ""))
    text = "\n".join(p for p in parts if p).strip()
    return text.replace("```python", "").replace("```", "").strip()


_DEF_KEYWORDS = r"def|class|func|fn|type|struct|interface|trait|enum|const|var|let"


def _grep_positions(sandbox, name, exts):
    """Seed positions for `name` by searching the code. Returns (def_hits, any_hits),
    each a list of (uri, line0, char). Definition-like lines (e.g. `def name`) are
    separated so callers can prefer them. Uses ripgrep when available, else a capped
    Python walk."""
    root = str(sandbox.root)
    def_hits, any_hits = [], []
    def_re = re.compile(r"\b(?:" + _DEF_KEYWORDS + r")\s+" + re.escape(name) + r"\b")

    def add(path, line0, char, text):
        (def_hits if def_re.search(text) else any_hits).append((path_to_uri(path), line0, char))

    rg = shutil.which("rg")
    if rg:
        globs = []
        for e in exts:
            globs += ["--glob", f"*{e}"]
        cmd = ["rg", "--json", "-w", "--max-count", "50", *globs, "--", name, root]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            for line in proc.stdout.splitlines():
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if obj.get("type") != "match":
                    continue
                d = obj["data"]
                subs = d.get("submatches") or [{}]
                add(d["path"]["text"], d["line_number"] - 1, subs[0].get("start", 0), d["lines"]["text"])
        except (subprocess.SubprocessError, OSError):
            pass
    else:
        word = re.compile(r"\b" + re.escape(name) + r"\b")
        scanned = 0
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in servers._SKIP_DIRS]
            for fn in fns:
                if exts and os.path.splitext(fn)[1].lower() not in exts:
                    continue
                try:
                    with open(os.path.join(dp, fn), encoding="utf-8", errors="ignore") as fh:
                        for i, line in enumerate(fh):
                            if word.search(line):
                                add(os.path.join(dp, fn), i, max(0, line.find(name)), line)
                except OSError:
                    continue
                scanned += 1
                if scanned > 3000 or len(def_hits) + len(any_hits) >= 80:
                    return def_hits, any_hits
    return def_hits, any_hits


def _seed_positions(client, sandbox, name, exts):
    """Candidate (uri, line, char) positions for `name`: workspace/symbol first
    (when the server supports it), then grep definition-like hits, then any hits."""
    query = name.split(".")[-1]
    positions, seen = [], set()

    def push(uri, line, char):
        key = (uri, line)
        if uri and key not in seen:
            seen.add(key)
            positions.append((uri, line, char))

    try:
        for s in client.workspace_symbol(query):
            if s.get("name") == query:
                u, ln, ch = _loc_pos(s.get("location") or {})
                push(u, ln, ch)
    except LspError:
        pass  # server doesn't support workspace/symbol (e.g. pylsp) — grep instead

    def_hits, any_hits = _grep_positions(sandbox, query, exts)
    for u, ln, ch in def_hits + any_hits:
        push(u, ln, ch)
    return positions, def_hits


def _flatten_symbols(symbols, acc=None):
    acc = acc if acc is not None else []
    for s in symbols or []:
        acc.append(s)
        if isinstance(s, dict) and s.get("children"):
            _flatten_symbols(s["children"], acc)
    return acc


def _symbol_line(s):
    if "location" in s:  # SymbolInformation (flat)
        return s["location"]["range"]["start"]["line"]
    rng = s.get("selectionRange") or s.get("range") or {}
    return (rng.get("start") or {}).get("line", 0)


def _lsp(args, sandbox):
    op = (args.get("op") or "").strip()
    symbol = (args.get("symbol") or "").strip()
    path = (args.get("path") or "").strip()
    root = str(sandbox.root)

    language = servers.detect_language(path) if path else None
    if language is None:
        language = servers.dominant_language(root)
    if language is None:
        langs = servers.available_languages()
        extra = f" (installed: {', '.join(langs)})" if langs else ""
        return f"No language server matches this project{extra}. Use `grep` instead."

    client = _client_for(root, language)
    if client is None:
        return f"No language server available for {language}. Use `grep` instead."

    exts = servers.LANGUAGE_SERVERS.get(language, (set(), []))[0]

    try:
        if op == "outline":
            if not path:
                return "ERROR: 'outline' needs a 'path'."
            uri = client.ensure_open(sandbox.resolve(path))
            out = []
            for s in _flatten_symbols(client.document_symbol(uri)):
                if s.get("kind") not in _OUTLINE_KINDS:
                    continue
                label = f"{_KIND.get(s.get('kind'), 'symbol')} {s.get('name', '')}".rstrip()
                out.append(f"{_rel(sandbox, uri)}:{_symbol_line(s) + 1}: {label}")
                if len(out) >= _MAX_RESULTS:
                    break
            return "\n".join(out) if out else f"(no top-level symbols found in {path})"

        if not symbol:
            return "ERROR: this op needs a 'symbol' name."

        positions, def_hits = _seed_positions(client, sandbox, symbol, exts)
        if not positions:
            return f"(no symbol named '{symbol}' found; try `grep`)"

        start = positions[0]
        client.ensure_open(uri_to_path(start[0]))

        # Ask the server to resolve the true definition from the seed position.
        def_positions = []
        try:
            for d in _as_list(client.definition(*start)):
                u, ln, ch = _loc_pos(d)
                if u:
                    def_positions.append((u, ln, ch))
        except LspError:
            pass

        if op == "defs":
            locs, seen = [], set()
            for u, ln, _ in (def_positions or [(x[0], x[1], 0) for x in def_hits] or positions):
                if (u, ln) in seen:
                    continue
                seen.add((u, ln))
                locs.append(_fmt(sandbox, u, ln, symbol))
                if len(locs) >= _MAX_RESULTS:
                    break
            return "\n".join(locs) if locs else f"(no definition for '{symbol}')"

        anchor = def_positions[0] if def_positions else start
        client.ensure_open(uri_to_path(anchor[0]))

        if op == "info":
            head = _fmt(sandbox, anchor[0], anchor[1], symbol)
            text = _hover_text(client.hover(*anchor))
            return head + ("\n" + text if text else "\n  (no type/hover info)")

        if op == "refs":
            out, seen = [], set()
            for r in client.references(*anchor):
                u, ln, _ = _loc_pos(r)
                if u is None or (u, ln) in seen:
                    continue
                seen.add((u, ln))
                out.append(_fmt(sandbox, u, ln, symbol))
                if len(out) >= _MAX_RESULTS:
                    break
            return "\n".join(out) if out else f"(no references to '{symbol}')"

        return f"ERROR: unknown op '{op}' (use defs|refs|info|outline)."
    except LspError as e:
        return f"ERROR: language server: {e}. Fall back to `grep`."


lsp_tool = Tool(
    name="lsp",
    description=(
        "Resolve a code symbol precisely with the project's language server (more accurate "
        "than grep for real code intelligence). ops: 'defs' (where a symbol is defined), "
        "'refs' (references/callers), 'info' (type/signature/doc via hover), 'outline' (all "
        "top-level symbols in a file — needs 'path'). Give the symbol NAME (e.g. 'Session.send' "
        "or 'send'), optionally a 'path' to disambiguate. Returns 'path:line: label' lines; then "
        "`read` those lines. If it reports no server for the language, use `grep`."
    ),
    parameters={
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": ["defs", "refs", "info", "outline"]},
            "symbol": {"type": "string", "description": "symbol name (for defs/refs/info)"},
            "path": {"type": "string", "description": "file to scope/disambiguate (required for outline)"},
        },
        "required": ["op"],
    },
    run=_lsp,
    read_only=True,
)
