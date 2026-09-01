"""TUI entry point — a Claude Code–style terminal interface for the local agent.

    python tui.py --project ./repo [--allow-exec] [--reasoning medium] [--show-reasoning]

Stdlib-only, fully local (talks only to your llama-server). This is the interactive
front-end; for a scriptable/pipe-friendly interface use cli.py instead.
"""

import argparse
import sys

from agent import config, inference
from agent.sandbox import Sandbox
from agent.tools import default_registry
from agent.ui import theme
from agent.ui.app import App


def main():
    # Make output UTF-8 first (so box glyphs work on Windows), enable ANSI on
    # legacy consoles, then let the theme re-detect color/unicode support.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    theme.enable_windows_vt()
    theme.refresh()

    ap = argparse.ArgumentParser(
        description="Local code agent — Claude Code–style TUI (gpt-oss via llama.cpp)"
    )
    ap.add_argument(
        "--project", default=config.PROJECT_ROOT, help="project root the tools may access"
    )
    ap.add_argument(
        "--reasoning", default=config.REASONING_EFFORT, choices=["low", "medium", "high"]
    )
    ap.add_argument(
        "--show-reasoning", action="store_true", help="show the model's thinking"
    )
    ap.add_argument("--quiet", action="store_true", help="hide the tool-call trace")
    ap.add_argument(
        "--allow-exec",
        action="store_true",
        help="enable the bash tool (runs shell commands; off by default)",
    )
    args = ap.parse_args()

    if args.allow_exec:
        config.ALLOW_EXEC = True

    # preflight: is the local server reachable?
    try:
        inference.health()
    except Exception as e:
        print(f"cannot reach llama.cpp at {config.BASE_URL}: {e}", file=sys.stderr)
        print(
            "start it: llama-server -m gpt-oss-20b.gguf -c 32768 --port 8081 -ngl 999",
            file=sys.stderr,
        )
        sys.exit(1)

    sandbox = Sandbox(args.project)
    registry = default_registry()  # includes `bash` iff config.ALLOW_EXEC
    n_ctx = inference.context_size() or config.CONTEXT_TOKENS

    App(
        sandbox,
        registry,
        n_ctx,
        reasoning=args.reasoning,
        show_reasoning=args.show_reasoning,
        quiet=args.quiet,
    ).run()


if __name__ == "__main__":
    main()
