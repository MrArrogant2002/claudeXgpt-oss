"""Renderers — turn agent events into styled terminal strings.

Pure functions (no printing) so they can be snapshot-tested offline with mock
events. app.py owns the actual output + spinner.
"""

import json
import re
import shutil

from .. import config
from .theme import GLYPH, paint


def _term_width(default=80):
    try:
        return max(40, shutil.get_terminal_size().columns)
    except Exception:
        return default


def _hn(n):
    """Human number: 1234 -> '1.2k', 45678 -> '45.7k'."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    if abs(n) < 1000:
        return str(n)
    return f"{n / 1000:.1f}k"


def _first_line(s, width=72):
    line = (s or "").strip().splitlines()[0] if (s or "").strip() else ""
    return line if len(line) <= width else line[: width - 1] + "…"


# --- header -----------------------------------------------------------------
def banner(project, model, ctx_window, exec_on):
    bar = paint("─" * min(_term_width(), 78), "dim")
    title = paint("local code agent", "accent", bold=True)
    meta = paint(f"{model} · {project} · ctx {_hn(ctx_window)}", "dim")
    exec_badge = (
        "  " + paint(f"{GLYPH['warn']} exec:on", "warn", bold=True)
        if exec_on
        else "  " + paint("exec:off", "dim")
    )
    hint = paint("type a question · /help for commands · /exit to quit", "dim")
    return f"{bar}\n{title}   {meta}{exec_badge}\n{hint}\n{bar}"


# --- tool call --------------------------------------------------------------
def _fmt_args(name, content):
    try:
        args = json.loads(content) if content else {}
    except (json.JSONDecodeError, TypeError):
        return _first_line(content, 80)
    if not isinstance(args, dict):
        return _first_line(content, 80)
    if name == "bash":
        return args.get("command", "")
    if name == "read":
        rng = ""
        s = args.get("start_line") or args.get("line_start")
        e = args.get("end_line") or args.get("line_end")
        if s and e:
            rng = f":{s}-{e}"
        elif s:
            rng = f":{s}"
        return f"{args.get('path', '')}{rng}"
    if name in ("grep", "glob"):
        p = args.get("pattern", args.get("query", ""))
        where = args.get("path", "")
        return f'"{p}"' + (f"  {where}" if where else "")
    if name == "list_dir":
        return args.get("path", ".") or "."
    # generic
    return "  ".join(f"{k}={v}" for k, v in args.items())


def tool_call(recipient, content):
    name = (recipient or "").split(".")[-1]
    bullet = paint(GLYPH["tool"], "tool", bold=True)
    label = paint(f"{name}", "tool", bold=True)
    args = paint(_fmt_args(name, content), "dim")
    return f"  {bullet} {label}  {args}"


# --- tool result ------------------------------------------------------------
def _summarize_result(recipient, content):
    name = (recipient or "").split(".")[-1]
    text = content or ""
    stripped = text.strip()
    if name == "bash":
        m = re.search(r"\[exit (\d+)\]", text)
        if m:
            code = int(m.group(1))
            tag = paint(f"exit {code}", "ok" if code == 0 else "err")
            # show a preview: stderr on failure, otherwise the first stdout line.
            se = re.search(r"--- stderr ---\n(.+)", text)
            so = re.search(r"--- stdout ---\n(.+)", text)
            src = se if (code != 0 and se) else (so or se)
            preview = "  " + paint(_first_line(src.group(1), 60), "dim") if src else ""
            return tag + preview
        if "timed out" in text:
            return paint("timed out", "err")
        if stripped.startswith("REFUSED"):
            return paint("refused", "err")
    if stripped.startswith("ERROR"):
        return paint(_first_line(stripped, 70), "err")
    # read: "# path  (lines a-b of N)"
    if stripped.startswith("#"):
        return paint(_first_line(stripped.lstrip("# "), 70), "dim")
    if stripped == "(no matches)":
        return paint("no matches", "dim")
    # grep/glob: count lines
    n = len([ln for ln in stripped.splitlines() if ln.strip()])
    if n:
        return paint(f"{n} line{'s' if n != 1 else ''}", "dim")
    return paint("(no output)", "dim")


def tool_result(recipient, content):
    branch = paint(GLYPH["result"], "dim")
    return f"    {branch} {_summarize_result(recipient, content)}"


# --- thinking / system ------------------------------------------------------
def thinking(content):
    head = paint(f"{GLYPH['think']} thinking", "think", italic=True)
    body = paint(_first_line(content, _term_width() - 6), "think", dim=True)
    return f"  {head}  {body}"


def system_note(content):
    return "  " + paint(str(content), "dim", italic=True)


# --- answer (lightweight terminal Markdown) ---------------------------------
_INLINE_RE = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`")


def _md_inline(s, base="fg"):
    """Render inline **bold** and `code`; strips the markers either way."""
    out, last = [], 0
    for m in _INLINE_RE.finditer(s):
        if m.start() > last:
            out.append(paint(s[last:m.start()], base))
        if m.group(1) is not None:
            out.append(paint(m.group(1), base, bold=True))
        else:
            out.append(paint(m.group(2), "second"))
        last = m.end()
    out.append(paint(s[last:], base))
    return "".join(out)


def _render_md_table(rows):
    """Turn Markdown `| a | b |` rows into aligned columns (no pipes)."""
    parsed = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    parsed = [c for c in parsed if not all((set(cell) <= set("-: ")) and cell for cell in c)]
    if not parsed:
        return []
    ncol = max(len(r) for r in parsed)
    for r in parsed:
        r += [""] * (ncol - len(r))
    strip_md = lambda x: re.sub(r"\*\*|`", "", x)
    avail = max(40, _term_width() - 4)
    colw = max(10, min(44, avail // ncol))

    def cell(x):
        c = strip_md(x)
        return c if len(c) <= colw else c[: colw - 1] + "…"

    widths = [min(colw, max(len(cell(r[i])) for r in parsed)) for i in range(ncol)]
    out = []
    for ri, r in enumerate(parsed):
        parts = [paint(cell(r[i]).ljust(widths[i]), "fg", bold=(ri == 0)) for i in range(ncol)]
        out.append("  " + "  ".join(parts).rstrip())
        if ri == 0:
            out.append("  " + paint("─" * min(avail, sum(widths) + 2 * (ncol - 1)), "dim"))
    return out


def _answer_md(text):
    lines = (text or "").rstrip().splitlines() or [""]
    out, table, in_code = [], [], False

    def flush():
        if table:
            out.extend(_render_md_table(table))
            table.clear()

    for ln in lines:
        s = ln.strip()
        if s.startswith("```"):
            flush()
            in_code = not in_code
            continue
        if in_code:
            flush()
            out.append(paint("    " + ln, "dim"))
            continue
        if s.startswith("|") and s.endswith("|") and s.count("|") >= 2:
            table.append(s)
            continue
        flush()
        if s.startswith("#"):
            out.append(paint(s.lstrip("# ").replace("**", ""), "accent", bold=True))
            continue
        m = re.match(r"^(\s*)[-*+]\s+(.*)", ln)
        if m:
            out.append(m.group(1) + paint("• ", "accent") + _md_inline(m.group(2)))
            continue
        if s.startswith(">"):
            out.append(paint("  " + s.lstrip("> "), "dim", italic=True))
            continue
        out.append(_md_inline(ln) if s else "")
    flush()
    return "\n".join(out)


def answer(text):
    """Render the model's reply with lightweight terminal Markdown. Falls back to
    plain fg on any error so a weird answer never breaks the display."""
    try:
        return _answer_md(text)
    except Exception:
        lines = (text or "").rstrip().splitlines() or [""]
        return "\n".join(paint(ln, "fg") for ln in lines)


def error_line(text):
    return paint(f"  ! {text}", "err")


# --- status bar -------------------------------------------------------------
def status_bar(*, d_prompt, d_new, d_out, used, window, session_total, calls, salvaged):
    segments = 8
    ratio = 0.0
    if window:
        ratio = max(0.0, min(1.0, used / window))
    filled = round(ratio * segments)
    over = ratio >= config.COMPACT_RATIO
    bar = paint(GLYPH["bar_full"] * filled, "warn" if over else "accent") + paint(
        GLYPH["bar_empty"] * (segments - filled), "dim"
    )
    ctx = f"{_hn(used)}/{_hn(window)} ctx {bar}"
    toks = f"{_hn(d_out)} out · {_hn(d_new)} new"
    sess = f"session {_hn(session_total)}"
    parts = [ctx, toks, f"{calls} call{'s' if calls != 1 else ''}", sess]
    if salvaged:
        parts.append(paint(f"salvaged {salvaged}", "warn"))
    branch = paint(GLYPH["branch"], "dim")
    return f"  {branch} " + paint(" · ", "dim").join(parts)
