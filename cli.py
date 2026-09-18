"""CLI entry point for the local code agent.

One-shot:     python cli.py --project /path/to/repo "Where is createUser defined?"
Interactive:  python cli.py --project /path/to/repo
Debug view:   add --show-reasoning (prints the analysis channel to stderr)

The final answer goes to STDOUT; tool-call trace + reasoning go to STDERR, so you
can pipe just the answer:  python cli.py "..." 2>/dev/null
"""

import argparse
import sys

from agent import config, harmony_codec as hc, inference, loop, permissions
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
    ap.add_argument(
        "--allow-exec",
        action="store_true",
        help="enable the `bash` tool (runs shell commands to compile/lint/test; "
        "OFF by default — only enable for code you trust to run on this machine)",
    )
    ap.add_argument(
        "--allow-edit",
        action="store_true",
        help="enable the write tools (edit/write/multi_edit); OFF by default",
    )
    ap.add_argument(
        "--init",
        action="store_true",
        help="build local_mind.md (the project map, like CLAUDE.md) and exit. "
        "Equivalent to passing `init` as the question.",
    )
    ap.add_argument(
        "--permission-mode",
        default=config.PERMISSION_MODE,
        choices=["plan", "default", "acceptEdits", "bypassPermissions", "dontAsk"],
        help="write-tier permission mode (default: plan = read-only). "
        "One-shot/headless can't prompt, so use acceptEdits to allow edits.",
    )
    args = ap.parse_args()

    if args.allow_exec:
        config.ALLOW_EXEC = True
    if args.allow_edit:
        config.ALLOW_EDIT = True

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
    registry = default_registry(project_root=str(sandbox.root))  # bash iff ALLOW_EXEC; lsp iff a server matches the repo
    n_ctx = inference.context_size() or config.CONTEXT_TOKENS
    print(f"[project] {sandbox.root}", file=sys.stderr)
    print(f"[context] window ~{n_ctx} tokens", file=sys.stderr)
    if config.ALLOW_EXEC:
        print(
            "[exec] ⚠  command execution ENABLED — the `bash` tool can run arbitrary "
            "shell commands (no container). Only use this on code you trust.",
            file=sys.stderr,
        )
    # Write tier: build the permission engine (headless = no interactive prompt, so
    # the mode decides; default `plan` is read-only). None when editing is disabled.
    engine = None
    if config.ALLOW_EDIT:
        engine = permissions.PermissionEngine(mode=args.permission_mode)
        print(
            f"[edit] ✎  write tools ENABLED — permission mode: {args.permission_mode} "
            "(plan = read-only). Edits are sandboxed, atomic, and backed up to "
            f"{config.EDIT_BACKUP_DIRNAME}/.",
            file=sys.stderr,
        )

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
        elif f.get("role") == "system":  # [compact] / [recover] notes
            if not args.quiet:
                print(f"  {f['content']}", file=sys.stderr)

    history = []

    def ask(q):
        nonlocal history
        before = inference.usage_snapshot()
        res, history = loop.run_turn(
            q,
            history,
            registry,
            sandbox,
            reasoning=args.reasoning,
            on_event=on_event,
            context_tokens=n_ctx,
            can_use_tool=(engine.can_use_tool if engine else None),
        )
        if res.reason == "completed":
            print(
                res.answer if res.answer else "(model returned an empty final answer)"
            )
        elif res.reason == "no_answer":
            print(
                "[no answer] The model kept returning an empty final even after being "
                "nudged. Try --reasoning high, raise AGENT_MAX_TOKENS, or ask a more "
                "specific question."
            )
        elif res.reason == "max_turns":
            print(
                f"[stopped: hit max turns ({config.MAX_TURNS})] The model kept calling "
                "tools without concluding. Raise AGENT_MAX_TURNS or narrow the question."
            )
        else:
            print(f"[stopped: {res.reason}] {res.answer}".rstrip())

        after = inference.usage_snapshot()
        d_prompt = after["prompt"] - before["prompt"]
        d_new = after["prompt_new"] - before["prompt_new"]
        d_out = after["output"] - before["output"]
        d_calls = after["calls"] - before["calls"]
        line = (
            f"[tokens] turn: {d_prompt:,} prompt ({d_new:,} newly evaluated) + {d_out:,} output "
            f"in {res.turns} turn(s) / {d_calls} model call(s) | "
            f"session total: {after['prompt'] + after['output']:,}"
        )
        salvaged = hc.salvage_count()
        if salvaged:
            line += f" | salvaged {salvaged} malformed header(s)"
        print(line, file=sys.stderr)

    # `local init` — scan the repo and write local_mind.md, then exit.
    do_init = args.init or (
        len(args.question) == 1 and args.question[0].lower() in ("init", "local-init")
    )
    if do_init:
        from agent import project_mind

        print(
            f"[init] scanning {sandbox.root} to build {project_mind.MIND_FILENAME} "
            "(this can take a few minutes on a large repo)…",
            file=sys.stderr,
        )
        res, _ = loop.run_turn(
            project_mind.INIT_PROMPT,
            [],
            registry,
            sandbox,
            reasoning="high",
            instructions=project_mind.INIT_INSTRUCTIONS,
            on_event=on_event,
            context_tokens=n_ctx,
            max_turns=max(config.MAX_TURNS, project_mind.INIT_MAX_TURNS),
            load_mind=False,  # don't feed the old map into the run that rebuilds it
        )
        if res.reason == "completed" and (res.answer or "").strip():
            path, sig = project_mind.save(sandbox.root, res.answer)
            print(
                f"[init] wrote {path} ({sig['file_count']} files scanned). "
                "Future queries will use it automatically.",
                file=sys.stderr,
            )
            print(str(path))  # stdout = the artifact path, for scripting
        else:
            print(
                f"[init] the model did not produce a document ({res.reason}). "
                "Try again with --reasoning high or a larger AGENT_MAX_TOKENS.",
                file=sys.stderr,
            )
            sys.exit(2)
        return

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
