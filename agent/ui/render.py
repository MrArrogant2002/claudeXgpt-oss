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
            emsg = ""
            se = re.search(r"--- stderr ---\n(.+)", text)
            if code != 0 and se:
                emsg = "  " + paint(_first_line(se.group(1), 60), "dim")
            return tag + emsg
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
    branch = paint(GLYPH["branch"], "dim")
    return f"    {branch} {_summarize_result(recipient, content)}"


# --- thinking / system ------------------------------------------------------
def thinking(content):
    head = paint(f"{GLYPH['think']} thinking", "think", italic=True)
    body = paint(_first_line(content, _term_width() - 6), "think", dim=True)
    return f"  {head}  {body}"


def system_note(content):
    return "  " + paint(str(content), "dim", italic=True)


# --- answer -----------------------------------------------------------------
def answer(text):
    # Print as-is (terminal soft-wraps); color the body in the primary fg so it
    # reads as "the model's reply" distinct from the dim tool trace.
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
