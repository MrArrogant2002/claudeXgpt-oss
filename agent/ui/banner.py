"""Startup banner (CLI-design Phase A) — the Claude Code–style header:
a coral pixel mascot, title + model line + cwd, a right-aligned server-health line,
and a left-gutter notice. Pure stdlib; colors via theme.paint (no-ops when disabled).
"""

import os
import shutil

from . import theme
from .theme import GLYPH, USE_UNICODE, paint

_VERSION = "0.4"

# A little coral creature (half-block sprite) with an ASCII fallback. 8 cells wide.
_MASCOT_UNI = [" ▄████▄ ", "██ ▀▀ ██", "██ ▄▄ ██", " ▀████▀ "]
_MASCOT_ASCII = ["  ____  ", "| o  o |", "|  --  |", "  ----  "]


def _hn(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    return str(n) if n < 1000 else f"{n / 1000:.0f}K"


def _short_path(path):
    home = os.path.expanduser("~")
    p = str(path)
    if p.startswith(home):
        p = "~" + p[len(home):]
    return p.replace("\\", "/")


def _netloc(base_url):
    return (base_url or "").split("//")[-1].rstrip("/") or "localhost"


def _term_width(default=80):
    try:
        return max(48, shutil.get_terminal_size().columns)
    except Exception:
        return default


def render(project, model, ctx_tokens, *, exec_on=False, edit_mode="off", base_url="", server_ok=False):
    mascot = _MASCOT_UNI if USE_UNICODE else _MASCOT_ASCII

    title = paint("local code agent", "accent", bold=True) + "  " + paint(f"v{_VERSION}", "dim")
    model_line = paint(
        f"{model} · {_hn(ctx_tokens)} context · fully local · "
        f"exec:{'on' if exec_on else 'off'} · edits:{edit_mode}",
        "dim",
    )
    cwd_line = paint(_short_path(project), "dim")
    headers = [title, model_line, cwd_line, ""]

    lines = []
    for m, h in zip(mascot, headers):
        lines.append(" " + paint(m, "accent") + "   " + h)

    # right-aligned server health line
    dot = GLYPH.get("dot", "●") if USE_UNICODE else "*"
    status = "connected" if server_ok else "unreachable"
    plain = f"{dot} llama-server {status} · {_netloc(base_url)}"
    colored = paint(dot, "ok" if server_ok else "err") + paint(
        f" llama-server {status} · {_netloc(base_url)}", "dim"
    )
    pad = max(0, min(_term_width(), 88) - len(plain))
    lines.append(" " * pad + colored)

    # left-gutter notice
    notice = "tokenizer offline (vendor/tiktoken) · /help for commands · /model to switch"
    lines.append(paint(GLYPH["gutter"], "dim") + " " + paint(notice, "dim"))

    return "\n".join(lines)
