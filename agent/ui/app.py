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
from . import render
from . import session as ptk_session
from .theme import CLEAR_LINE, CR, GLYPH, SPINNER, paint


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
        # Rich input (history + autocomplete) when prompt_toolkit is present AND
        # we're on a real terminal; otherwise fall back to stdlib input().
        self.session = None
        try:
            is_tty = self.out.isatty()
        except Exception:
            is_tty = False
        if ptk_session.HAS_PTK and is_tty:
            self.session = ptk_session.build_session(self._history_path())

    def _p(self, s=""):
        self.out.write(s + "\n")
        self.out.flush()

    def _history_path(self):
        return os.path.join(os.path.expanduser("~"), ".agent_tui_history")

    def _toolbar(self):
        used = inference.usage_snapshot().get("last_prompt", 0)
        exec_state = "on" if config.ALLOW_EXEC else "off"
        return (
            f"  reasoning:{self.reasoning} · exec:{exec_state} · "
            f"ctx {used}/{self.n_ctx} · ^C interrupt · /help"
        )

    def _read_line(self):
        if self.session is not None:
            return ptk_session.read(self.session, self._toolbar).strip()
        return input(paint(f"\n{GLYPH['prompt']} ", "accent", bold=True)).strip()

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
        else:
            self._p(render.system_note(f"unknown command: /{cmd}  (try /help)"))
        return True

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
            ("/tokens", "show session token usage"),
            ("/exit", "quit"),
        ]
        self._p()
        for cmd, desc in rows:
            self._p("  " + paint(f"{cmd:<28}", "accent") + paint(desc, "dim"))

    # --- main loop ----------------------------------------------------------
    def run(self):
        self._p(
            render.banner(
                str(self.sandbox.root), config.MODEL, self.n_ctx, config.ALLOW_EXEC
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
