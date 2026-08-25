"""CLI entry point for the local code agent.

One-shot:     python cli.py --project /path/to/repo "Where is createUser defined?"
Interactive:  python cli.py --project /path/to/repo
Debug view:   add --show-reasoning (prints the analysis channel to stderr)

The final answer goes to STDOUT; tool-call trace + reasoning go to STDERR, so you
can pipe just the answer:  python cli.py "..." 2>/dev/null
"""

import argparse
import sys

from agent import config, inference, loop
from agent.sandbox import Sandbox
from agent.tools import default_registry


def main():
    # Make stdout/stderr robust to non-ASCII on Windows consoles (cp1252),
    # so answers/file contents with em-dashes, emoji, etc. don't crash printing.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(
        description="Local code agent (gpt-oss brain via llama.cpp)"
    )
    ap.add_argument(
        "question", nargs="*", help="one-shot question; omit for interactive REPL"
    )
    ap.add_argument(
        "--project",
        default=config.PROJECT_ROOT,
        help="project root the tools may access",
    )
    ap.add_argument(
        "--reasoning",
        default=config.REASONING_EFFORT,
        choices=["low", "medium", "high"],
    )
    ap.add_argument(
        "--show-reasoning",
        action="store_true",
        help="print the analysis channel (debug)",
    )
    ap.add_argument("--quiet", action="store_true", help="hide the tool-call trace")
    args = ap.parse_args()

    # preflight: is the server reachable?
    try:
        inference.health()
    except Exception as e:
        print(
            f"[error] cannot reach llama.cpp at {config.BASE_URL}: {e}", file=sys.stderr
        )
        print(
            "Start it: llama-server -m gpt-oss-20b.gguf -c 8192 --port 8081 -ngl 999",
            file=sys.stderr,
        )
        sys.exit(1)

    sandbox = Sandbox(args.project)
    registry = default_registry()
    print(f"[project] {sandbox.root}", file=sys.stderr)

    def on_event(f):
        ch = f.get("channel")
        if f.get("role") == "tool":
            if not args.quiet:
                print(
                    f"  [tool result] {f['recipient']}: {f['content'][:160]!r}",
                    file=sys.stderr,
                )
        elif ch == "commentary" and f.get("recipient"):
            if not args.quiet:
                print(
                    f"  [tool call]   {f['recipient']} {f['content'][:160]}",
                    file=sys.stderr,
                )
        elif ch == "analysis" and args.show_reasoning:
            print(f"  [reasoning]   {f['content']}", file=sys.stderr)

    history = []

    def ask(q):
        nonlocal history
        res, history = loop.run_turn(
            q, history, registry, sandbox, reasoning=args.reasoning, on_event=on_event
        )
        if res.reason == "completed":
            print(
                res.answer if res.answer else "(model returned an empty final answer)"
            )
        else:
            print(f"[stopped: {res.reason}] {res.answer}".rstrip())

    # one-shot
    if args.question:
        ask(" ".join(args.question))
        return

    # interactive REPL
    print("Local code agent — type a question ('exit' to quit).", file=sys.stderr)
    while True:
        try:
            q = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if q.lower() in {"exit", "quit"}:
            break
        if q:
            ask(q)


if __name__ == "__main__":
    main()
