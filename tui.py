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
    ap.add_argument(
        "--no-stream",
        action="store_true",
        help="disable token-by-token streaming (falls back to whole-turn output)",
    )
    ap.add_argument(
        "--allow-edit",
        action="store_true",
        help="enable the write tools (edit/write/multi_edit); off by default",
    )
    ap.add_argument(
        "--permission-mode",
        default=config.PERMISSION_MODE,
        choices=["plan", "default", "acceptEdits", "bypassPermissions", "dontAsk"],
        help="write-tier permission mode (default: plan = read-only)",
    )
    args = ap.parse_args()

    if args.allow_exec:
        config.ALLOW_EXEC = True
    if args.allow_edit:
        config.ALLOW_EDIT = True

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
    registry = default_registry(project_root=str(sandbox.root))  # bash/edit tools per config; lsp iff a server matches the repo
    n_ctx = inference.context_size() or config.CONTEXT_TOKENS

    # Write tier: in the interactive TUI, enabling edits defaults to ASKING per edit
    # (M5) instead of the headless-safe `plan`. App builds the engine with an
    # interactive prompter from this mode.
    permission_mode = None
    if config.ALLOW_EDIT:
        permission_mode = args.permission_mode
        if permission_mode == "plan":  # interactive: ask per edit, don't silently block
            permission_mode = "default"

    App(
        sandbox,
        registry,
        n_ctx,
        reasoning=args.reasoning,
        show_reasoning=args.show_reasoning,
        quiet=args.quiet,
        streaming=not args.no_stream,
        permission_mode=permission_mode,
    ).run()


if __name__ == "__main__":
    main()
