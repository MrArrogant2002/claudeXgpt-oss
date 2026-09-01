"""Theme: colors, glyphs, and ANSI styling — stdlib only.

Palette: "Teal / Violet on Slate" (deliberately NOT Claude's coral). Truecolor
(24-bit) with a graceful downgrade to no-color when the stream isn't a TTY, when
NO_COLOR is set, or on a dumb terminal. Glyphs fall back to ASCII if the output
encoding can't represent them.
"""

import os
import sys

# --- capability detection ---------------------------------------------------
def _supports_color(stream) -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def _supports_unicode(stream) -> bool:
    enc = getattr(stream, "encoding", None) or "ascii"
    try:
        "✻⏺└›▓░⚠".encode(enc)
        return True
    except Exception:
        return False


def enable_windows_vt():
    """Best-effort enable ANSI escape processing on legacy Windows consoles."""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VT
    except Exception:
        pass


USE_COLOR = _supports_color(sys.stdout)
USE_UNICODE = _supports_unicode(sys.stdout)


def refresh():
    """Re-detect color/unicode support and rebuild glyphs. Call after switching
    stdout's encoding (e.g. to UTF-8) or enabling VT so detection is accurate."""
    global USE_COLOR, USE_UNICODE, GLYPH, SPINNER
    USE_COLOR = _supports_color(sys.stdout)
    USE_UNICODE = _supports_unicode(sys.stdout)
    GLYPH = _build_glyphs()
    SPINNER = _build_spinner()

# --- palette (r, g, b) — Teal / Violet on Slate -----------------------------
PALETTE = {
    "accent": (45, 212, 191),   # teal 400  — user prompt, spinner, highlights
    "tool": (56, 189, 248),     # sky 400   — tool-call bullet + name
    "second": (167, 139, 250),  # violet 400— secondary accents
    "think": (148, 163, 184),   # slate 400 — reasoning
    "ok": (74, 222, 128),       # green 400 — exit 0 / success
    "err": (251, 113, 133),     # rose 400  — errors / exit != 0 / refused
    "warn": (251, 191, 36),     # amber 400 — warnings, exec badge, near-limit ctx
    "dim": (100, 116, 139),     # slate 500 — metadata, borders, summaries
    "fg": (226, 232, 240),      # slate 200 — primary text
}

RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_ITALIC = "\x1b[3m"


def _fg(rgb) -> str:
    r, g, b = rgb
    return f"\x1b[38;2;{r};{g};{b}m"


def paint(text, color=None, *, bold=False, dim=False, italic=False) -> str:
    """Wrap `text` in ANSI styles (no-op when color is disabled)."""
    if not USE_COLOR:
        return text
    codes = ""
    if color is not None:
        codes += _fg(PALETTE[color] if isinstance(color, str) else color)
    if bold:
        codes += _BOLD
    if dim:
        codes += _DIM
    if italic:
        codes += _ITALIC
    if not codes:
        return text
    return f"{codes}{text}{RESET}"


# --- glyphs (unicode with ASCII fallback) -----------------------------------
def _build_glyphs():
    def g(uni, ascii_):
        return uni if USE_UNICODE else ascii_

    return {
        "prompt": g("›", ">"),
        "think": g("✻", "*"),
        "tool": g("⏺", "*"),
        "branch": g("└", "`-"),
        "warn": g("⚠", "!"),
        "bar_full": g("▓", "#"),
        "bar_empty": g("░", "."),
        "expand": g("▸", ">"),
    }


def _build_spinner():
    if USE_UNICODE:
        return ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    return ["|", "/", "-", "\\"]


GLYPH = _build_glyphs()
SPINNER = _build_spinner()

# ANSI line controls
CLEAR_LINE = "\x1b[K"  # clear from cursor to end of line
CR = "\r"
