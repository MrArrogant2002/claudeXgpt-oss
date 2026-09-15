"""The interactive REPL: read a question, run one agent turn on a worker thread,
render events live (spinner + tool blocks), print the answer + status bar.

Stdlib only. Rendering marshals through a queue so all output happens on the main
thread; the agent turn runs on a background thread so the UI stays responsive and
Ctrl-C can request cancellation.
"""

import os
import queue
import sys
import threading
import time

from .. import config, inference, loop
from .. import harmony_codec as hc
from ..tools import default_registry
from . import banner, render
from . import session as ptk_session
from .theme import CLEAR_LINE, CR, GLYPH, SPINNER, paint

# Rotating ghost placeholders shown in the empty input (Claude-style).
_PLACEHOLDERS = [
    'Try "explain how Session.send works"',
    'Try "where is the retry logic defined?"',
    'Try "compile and fix the failing test"',
    'Try "what does this module do, in 3 lines?"',
]


class _Spinner:
    def __init__(self, stream):
        self.stream = stream
        self.i = 0
        self.t0 = time.time()
        self.active = False

    def tick(self):
        frame = SPINNER[self.i % len(SPINNER)]
        self.i += 1
        el = time.time() - self.t0
        msg = paint(f"{frame} thinking… ", "accent") + paint(f"({el:.1f}s)", "dim")
        self.stream.write(CR + msg + CLEAR_LINE)
        self.stream.flush()
        self.active = True

    def clear(self):
        if self.active:
            self.stream.write(CR + CLEAR_LINE)
            self.stream.flush()
            self.active = False


class App:
    def __init__(
        self,
        sandbox,
        registry,
        n_ctx,
        *,
        reasoning,
        show_reasoning,
        quiet,
        stream=None,
        streaming=True,
        permission_mode=None,
    ):
        self.sandbox = sandbox
        self.registry = registry
        self.n_ctx = n_ctx
        self.reasoning = reasoning
        self.show_reasoning = show_reasoning
        self.quiet = quiet
        self.streaming = streaming  # token-by-token output (P3)
        self.history = []
        self.out = stream or sys.stdout
        self._events_q = None  # set per-turn; lets the permission prompter reach the queue
        self._cancel = None
        self._engine = None  # write-tier permission engine (built below iff editing)
        self._ph_i = 0  # rotating-placeholder index
        # Rich input (history + autocomplete) when prompt_toolkit is present AND
        # we're on a real terminal; otherwise fall back to stdlib input().
        self.session = None
        try:
            is_tty = self.out.isatty()
        except Exception:
            is_tty = False
        if ptk_session.HAS_PTK and is_tty:
            self.session = ptk_session.build_session(
                self._history_path(),
                on_shift_tab=self._cycle_mode,
                on_help=self._print_shortcuts,
            )
        # Write-tier permission engine with an INTERACTIVE prompter (M5). Built only
        # when editing is enabled; the prompter marshals the ask onto the main thread.
        self.can_use_tool = None
        self.permission_mode = permission_mode
        if permission_mode:
            from ..permissions import PermissionEngine

            self._engine = PermissionEngine(mode=permission_mode, prompter=self._request_permission)
            self.can_use_tool = self._engine.can_use_tool

    def _p(self, s=""):
        self.out.write(s + "\n")
        self.out.flush()

    def _history_path(self):
        return os.path.join(os.path.expanduser("~"), ".agent_tui_history")

    _MODE_LABEL = {
        "plan": "plan", "default": "ask-edits", "acceptEdits": "accept-edits",
        "bypassPermissions": "bypass", "dontAsk": "auto",
    }

    def _toolbar(self):
        used = inference.usage_snapshot().get("last_prompt", 0)
        parts = []
        if self.permission_mode:
            parts.append(f"{GLYPH['warn']} {self._MODE_LABEL.get(self.permission_mode, self.permission_mode)}")
        else:
            parts.append("read-only")
        if config.ALLOW_EXEC:
            parts.append("exec:on")
        parts.append(f"ctx {render._hn(used)}/{render._hn(self.n_ctx)}")
        if self.permission_mode:
            parts.append("shift-tab: mode")
        parts.append("? · /help")
        return "  " + " · ".join(parts)

    def _cycle_mode(self):
        """Shift+Tab: cycle the write-tier permission mode (plan → ask → accept)."""
        if self._engine is None:
            return
        order = ["plan", "default", "acceptEdits"]
        try:
            i = order.index(self.permission_mode)
        except ValueError:
            i = -1
        self.permission_mode = order[(i + 1) % len(order)]
        self._engine.mode = self.permission_mode

    def _read_line(self):
        if self.session is not None:
            ph = _PLACEHOLDERS[self._ph_i % len(_PLACEHOLDERS)]
            self._ph_i += 1
            return ptk_session.read(self.session, self._toolbar, placeholder=ph).strip()
        return input(paint(f"\n{GLYPH['prompt']} ", "accent", bold=True)).strip()

    # --- interactive permission approval (M5) -------------------------------
    def _request_permission(self, tool_name, args, spec):
        """Called from the WORKER thread by the permission engine. Marshals the ask
        onto the main thread via the event queue and blocks (cancel-aware) for the
        answer. Returns 'allow_once'|'allow_session'|'always'|'deny'."""
        done = threading.Event()
        box = {}
        self._events_q.put(
            {"_permission": True, "tool": tool_name, "args": args, "done": done, "box": box}
        )
        while not done.wait(0.1):  # poll so Ctrl-C (cancel) can't deadlock us
            if self._cancel is not None and self._cancel.is_set():
                return "deny"
        return box.get("answer", "deny")

    @staticmethod
    def _map_permission_answer(raw):
        r = (raw or "").strip().lower()
        if r in ("y", "yes", "o", "once", "1"):
            return "allow_once"
        if r in ("s", "session"):
            return "allow_session"
        if r in ("a", "always"):
            return "always"
        return "deny"  # n / no / d / empty / anything else -> fail-closed

    def _permission_preview(self, tool_name, args):
        """One-line summary of the pending change for the approval prompt."""
        path = args.get("path", "?")
        if tool_name == "write":
            n = len((args.get("content") or "").encode("utf-8", "replace"))
            return f"write {path}  ({n} bytes)"
        if tool_name == "multi_edit":
            return f"multi_edit {path}  ({len(args.get('edits') or [])} edits)"
        old = (args.get("old_string") or "").strip().splitlines()
        new = (args.get("new_string") or "").strip().splitlines()
        head = old[0][:50] if old else ""
        tail = new[0][:50] if new else ""
        return f"edit {path}  «{head}» → «{tail}»"

    def _compute_edit_preview(self, tool_name, args):
        """Best-effort unified diff of what the pending edit WOULD do (or None)."""
        try:
            from .. import edits

            p = self.sandbox.resolve(args.get("path", ""))
            current = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
            if tool_name == "write":
                updated = args.get("content", "")
            elif tool_name == "multi_edit":
                updated = current
                for e in args.get("edits", []) or []:
                    old = e.get("old_string")
                    if not old or old not in updated:
                        return None
                    new = e.get("new_string", "")
                    updated = updated.replace(old, new) if e.get("replace_all") else updated.replace(old, new, 1)
            else:  # edit
                old = args.get("old_string")
                if not old or old not in current:
                    return None
                new = args.get("new_string", "")
                updated = current.replace(old, new) if args.get("replace_all") else current.replace(old, new, 1)
            rel = str(self.sandbox.relativize(p)).replace("\\", "/")
            return edits.unified_diff(current, updated, rel)
        except Exception:
            return None

    def _render_diff_box(self, op, rel, diff):
        w = min(render._term_width(), 78)
        head = f"┌─ permission · {op} "
        rule = "─" * max(4, w - len(head) - len(rel) - 1)
        self._p("  " + paint(head, "warn") + paint(rel, "fg") + " " + paint(rule, "warn"))
        lines = [ln.rstrip("\n") for ln in diff.splitlines() if not ln.startswith(("--- ", "+++ "))]
        for ln in lines[:14]:
            color = "dim"
            if ln.startswith("@@"):
                color = "dim"
            elif ln.startswith("+"):
                color = "ok"
            elif ln.startswith("-"):
                color = "err"
            self._p("  " + paint("│ ", "warn") + paint(ln[: w - 4], color))
        if len(lines) > 14:
            self._p("  " + paint("│ ", "warn") + paint(f"… {len(lines) - 14} more lines", "dim"))
        self._p("  " + paint("└" + "─" * (w - 1), "warn"))

    def _prompt_user_for_permission(self, tool_name, args):
        """Main-thread: show the pending change (as a diff box) and read the choice."""
        self._p()
        diff = self._compute_edit_preview(tool_name, args)
        if diff and diff.strip():
            self._render_diff_box(tool_name, args.get("path", "?"), diff)
        else:
            self._p("  " + paint(f"{GLYPH['warn']} permission", "warn", bold=True)
                    + "  " + paint(self._permission_preview(tool_name, args), "fg"))
        prompt = paint("  allow? ", "accent", bold=True) + paint(
            "[y] once  [s] session  [a] always  [N] no › ", "dim"
        )
        try:
            raw = input(prompt)
        except (EOFError, KeyboardInterrupt):
            if self._cancel is not None:
                self._cancel.set()
            return "deny"
        return self._map_permission_answer(raw)

    # --- event rendering ----------------------------------------------------
    def _render_event(self, f):
        role = f.get("role")
        ch = f.get("channel")
        if role == "tool":
            if not self.quiet:
                self._p(render.tool_result(f.get("recipient"), f.get("content")))
        elif ch == "commentary" and f.get("recipient"):
            if not self.quiet:
                self._p(render.tool_call(f.get("recipient"), f.get("content")))
        elif ch == "analysis":
            if self.show_reasoning:
                self._p(render.thinking(f.get("content")))
        elif role == "system":
            if not self.quiet:
                self._p(render.system_note(f.get("content")))
        # channel == "final" is ignored here; the answer prints from the Result.

    def _handle_item(self, f, spin, state):
        """Render one queued item: a streaming delta (types out live) or a full
        event (tool block, system note). Mutates `state` to track open live lines."""
        if f.get("_permission"):
            spin.clear()
            if state["answering"] or state["thinking"]:
                self._p()
                state["answering"] = state["thinking"] = False
            if self._cancel is not None and self._cancel.is_set():
                answer = "deny"  # tearing down — don't prompt
            else:
                answer = self._prompt_user_for_permission(f["tool"], f.get("args") or {})
            f["box"]["answer"] = answer
            f["done"].set()
            return
        if f.get("_delta"):
            ch, text = f.get("channel"), f.get("content") or ""
            if not text:
                return
            if ch == "final":
                if not state["answering"]:
                    spin.clear()
                    self._p()  # blank line before the answer
                    state["answering"] = True
                    state["streamed_final"] = True
                self.out.write(paint(text, "fg"))
                self.out.flush()
            elif ch == "analysis" and self.show_reasoning:
                if not state["thinking"]:
                    spin.clear()
                    self.out.write("  " + paint(f"{GLYPH['think']} ", "think", italic=True))
                    state["thinking"] = True
                self.out.write(paint(text, "think", dim=True))
                self.out.flush()
            return
        # A full event: close any open streamed line first.
        spin.clear()
        if state["answering"] or state["thinking"]:
            self._p()
            state["answering"] = state["thinking"] = False
        if self.streaming and f.get("channel") == "analysis":
            return  # already streamed live via deltas
        self._render_event(f)

    # --- one turn -----------------------------------------------------------
    def ask(self, q):
        before = inference.usage_snapshot()
        events_q = queue.Queue()
        result = {}
        cancel = threading.Event()
        self._events_q = events_q  # let the permission prompter marshal onto the queue
        self._cancel = cancel

        def on_delta(channel, text):
            events_q.put({"_delta": True, "channel": channel, "content": text})

        def worker():
            try:
                res, hist = loop.run_turn(
                    q,
                    self.history,
                    self.registry,
                    self.sandbox,
                    reasoning=self.reasoning,
                    on_event=events_q.put,
                    context_tokens=self.n_ctx,
                    cancel=cancel,
                    stream=self.streaming,
                    on_delta=on_delta,
                    can_use_tool=self.can_use_tool,
                )
                result["res"], result["hist"] = res, hist
            except Exception as e:  # never let the worker kill the REPL
                result["err"] = e

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        spin = _Spinner(self.out)
        state = {"answering": False, "thinking": False, "streamed_final": False}
        interrupted = False
        try:
            while t.is_alive() or not events_q.empty():
                try:
                    f = events_q.get(timeout=0.1)
                except queue.Empty:
                    if t.is_alive() and not (state["answering"] or state["thinking"]):
                        spin.tick()
                    continue
                self._handle_item(f, spin, state)
        except KeyboardInterrupt:
            interrupted = True
            cancel.set()
            spin.clear()
            if state["answering"] or state["thinking"]:
                self._p()
                state["answering"] = state["thinking"] = False
            self._p(render.system_note("interrupting…"))
        finally:
            try:
                t.join()
            except KeyboardInterrupt:
                pass
            spin.clear()
            try:
                while True:
                    self._handle_item(events_q.get_nowait(), spin, state)
            except queue.Empty:
                pass
            if state["answering"] or state["thinking"]:
                self._p()  # close any open streamed line

        self.history = result.get("hist", self.history)
        self._print_outcome(result, interrupted, state["streamed_final"])
        self._print_status(before)

    def _print_outcome(self, result, interrupted, streamed=False):
        if "err" in result:
            self._p(render.error_line(f"error: {result['err']}"))
            return
        res = result.get("res")
        if res is None:
            self._p(render.system_note("cancelled." if interrupted else "no result."))
            return
        if res.reason == "completed":
            if streamed:
                return  # the answer already typed out live during the turn
            self._p()
            self._p(render.answer(res.answer or "(empty answer)"))
        elif res.reason == "cancelled":
            self._p(render.system_note("cancelled."))
        elif res.reason == "no_answer":
            self._p(
                render.error_line(
                    "no answer — try /reasoning high or a narrower question."
                )
            )
        elif res.reason == "max_turns":
            self._p(render.error_line(f"stopped: hit max turns ({config.MAX_TURNS})."))
        else:
            self._p(render.error_line(f"stopped: {res.reason} {res.answer}".rstrip()))

    def _print_status(self, before):
        after = inference.usage_snapshot()
        self._p(
            render.status_bar(
                d_prompt=after["prompt"] - before["prompt"],
                d_new=after["prompt_new"] - before["prompt_new"],
                d_out=after["output"] - before["output"],
                used=after.get("last_prompt", 0),
                window=self.n_ctx,
                session_total=after["prompt"] + after["output"],
                calls=after["calls"] - before["calls"],
                salvaged=hc.salvage_count(),
            )
        )

    # --- slash commands -----------------------------------------------------
    def handle_command(self, line):
        """Return False to quit the REPL, True to continue."""
        parts = line[1:].split()
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].lower() if len(parts) > 1 else ""
        if cmd in ("exit", "quit", "q"):
            return False
        elif cmd == "help":
            self._print_help()
        elif cmd == "clear":
            self.history = []
            self.out.write("\x1b[2J\x1b[H")  # clear screen + home
            self._p(render.system_note("history cleared."))
        elif cmd == "reasoning":
            if arg in ("low", "medium", "high"):
                self.reasoning = arg
                self._p(render.system_note(f"reasoning = {arg}"))
            else:
                self._p(
                    render.system_note(
                        f"reasoning is {self.reasoning}  (use: /reasoning low|medium|high)"
                    )
                )
        elif cmd in ("show-reasoning", "think"):
            self.show_reasoning = not self.show_reasoning
            self._p(render.system_note(f"show reasoning = {self.show_reasoning}"))
        elif cmd == "exec":
            self._toggle_exec(arg)
        elif cmd == "tokens":
            u = inference.usage_snapshot()
            self._p(
                render.system_note(
                    f"session: {u['prompt'] + u['output']} total · {u['output']} out · "
                    f"{u['calls']} calls · salvaged {hc.salvage_count()}"
                )
            )
        elif cmd == "mode":
            self._set_mode(arg)
        elif cmd == "model":
            self._print_model_info()
        elif cmd in ("shortcuts", "keys"):
            self._print_shortcuts()
        else:
            self._p(render.system_note(f"unknown command: /{cmd}  (try /help)"))
        return True

    def _set_mode(self, arg):
        aliases = {
            "plan": "plan", "ask": "default", "default": "default",
            "accept": "acceptEdits", "acceptedits": "acceptEdits",
            "bypass": "bypassPermissions", "auto": "dontAsk",
        }
        if self._engine is None:
            self._p(render.system_note("editing is off — start with --allow-edit to use permission modes."))
            return
        target = aliases.get(arg)
        if target is None:
            cur = self._MODE_LABEL.get(self.permission_mode, self.permission_mode)
            self._p(render.system_note(f"mode is {cur}  (use: /mode plan|ask|accept, or Shift+Tab)"))
            return
        self.permission_mode = target
        self._engine.mode = target
        self._p(render.system_note(f"mode = {self._MODE_LABEL.get(target, target)}"))

    def _print_model_info(self):
        rows = [
            ("model", config.MODEL),
            ("context", f"{render._hn(self.n_ctx)} tokens"),
            ("server", config.BASE_URL),
            ("reasoning", self.reasoning),
            ("tokenizer", "vendored o200k · offline"),
        ]
        self._p()
        self._p("  " + paint("model", "accent", bold=True))
        for k, v in rows:
            self._p("  " + paint(f"{k:<11}", "warn") + paint(str(v), "fg"))
        self._p("  " + paint("to switch models, restart llama-server with a different GGUF.", "dim"))

    def _print_shortcuts(self):
        rows = [
            ("Enter", "submit   ·   Shift+Enter / Alt+Enter   newline"),
            ("Shift+Tab", "cycle permission mode (plan → ask → accept)"),
            ("Esc", "interrupt the running turn"),
            ("Ctrl+C", "cancel input / interrupt   ·   Ctrl+D   quit"),
            ("↑ / ↓", "input history   ·   Ctrl+L   clear screen"),
            ("?", "this help   ·   /  commands"),
        ]
        self._p()
        self._p("  " + paint("shortcuts", "accent", bold=True))
        for k, d in rows:
            self._p("  " + paint(f"{k:<11}", "warn") + paint(d, "dim"))

    def _toggle_exec(self, arg):
        want = {"on": True, "1": True, "true": True, "off": False, "0": False, "false": False}.get(arg)
        if want is None:
            state = "on" if config.ALLOW_EXEC else "off"
            self._p(render.system_note(f"exec is {state}  (use: /exec on|off)"))
            return
        config.ALLOW_EXEC = want
        self.registry = default_registry()  # add/remove the bash tool
        self._p(render.system_note(f"exec = {'on ⚠' if want else 'off'}"))

    def _print_help(self):
        rows = [
            ("/help", "show this help"),
            ("/clear", "clear the screen and conversation history"),
            ("/reasoning low|medium|high", "set reasoning effort"),
            ("/show-reasoning", "toggle showing the model's thinking"),
            ("/exec on|off", "enable/disable the bash (run code) tool"),
            ("/mode plan|ask|accept", "set the write permission mode (or Shift+Tab)"),
            ("/model", "show model / server info"),
            ("/tokens", "show session token usage"),
            ("/shortcuts", "keyboard shortcuts (or press ?)"),
            ("/exit", "quit"),
        ]
        self._p()
        for cmd, desc in rows:
            self._p("  " + paint(f"{cmd:<28}", "accent") + paint(desc, "dim"))

    # --- main loop ----------------------------------------------------------
    def run(self):
        try:
            inference.health()
            server_ok = True
        except Exception:
            server_ok = False
        self._p(
            banner.render(
                str(self.sandbox.root),
                config.MODEL,
                self.n_ctx,
                exec_on=config.ALLOW_EXEC,
                edit_mode=self._MODE_LABEL.get(self.permission_mode, "off"),
                base_url=config.BASE_URL,
                server_ok=server_ok,
            )
        )
        while True:
            try:
                line = self._read_line()
            except EOFError:
                self._p()
                break
            except KeyboardInterrupt:
                self._p()
                continue
            if not line:
                continue
            if line.startswith("/"):
                if not self.handle_command(line):
                    break
                continue
            self.ask(line)
        self._p(render.system_note("bye."))
