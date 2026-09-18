"""prompt_toolkit input layer (P2) — history, slash-command autocomplete, a
bottom toolbar, and an amber-styled prompt.

Imported lazily and defensively: if prompt_toolkit isn't installed (or the output
isn't a real terminal), HAS_PTK is False and the app falls back to stdlib input().
This keeps the TUI runnable with zero extra packages, and richer when the one
optional dependency is present.
"""

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style
    from prompt_toolkit.application import run_in_terminal
    from prompt_toolkit.application.current import get_app
    from prompt_toolkit.filters import Condition

    HAS_PTK = True
except Exception:  # prompt_toolkit not installed
    HAS_PTK = False


# Slash commands offered by autocomplete (label -> one-line meta).
COMMANDS = {
    "/help": "show help",
    "/init": "scan the repo and build local_mind.md (project map)",
    "/reasoning": "set reasoning effort: low | medium | high",
    "/show-reasoning": "toggle showing the model's thinking",
    "/exec": "enable/disable the bash (run code) tool: on | off",
    "/mode": "set the write permission mode: plan | ask | accept",
    "/model": "show model / server info",
    "/tokens": "show session token usage",
    "/shortcuts": "keyboard shortcuts",
    "/clear": "clear the screen and conversation history",
    "/exit": "quit",
}


if HAS_PTK:

    class _SlashCompleter(Completer):
        """Complete slash commands only when the line starts with '/'."""

        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            if not text.startswith("/"):
                return
            head = text.split(" ", 1)[0]  # complete the command word only
            if " " in text:
                return
            for cmd, meta in COMMANDS.items():
                if cmd.startswith(head):
                    yield Completion(
                        cmd, start_position=-len(head), display=cmd, display_meta=meta
                    )

    _STYLE = Style.from_dict(
        {
            "prompt": "#d97757 bold",  # coral (Claude-style)
            "bottom-toolbar": "#b8b3ad bg:#2a2724",
        }
    )

    def build_session(history_path, on_shift_tab=None, on_help=None):
        """Return a configured PromptSession, or None on failure. `on_shift_tab` cycles
        the write-tier permission mode; `on_help` shows the shortcuts overlay when the
        user presses `?` on an empty line (a non-empty line inserts a literal `?`)."""
        try:
            kb = KeyBindings()
            if on_shift_tab is not None:

                @kb.add("s-tab")
                def _cycle(event):
                    try:
                        on_shift_tab()
                    except Exception:
                        pass
                    event.app.invalidate()  # redraw the bottom toolbar with the new mode

            if on_help is not None:

                @kb.add("?", filter=Condition(lambda: not get_app().current_buffer.text))
                def _help(event):
                    run_in_terminal(lambda: (on_help(), None)[1])

            return PromptSession(
                history=FileHistory(history_path),
                completer=_SlashCompleter(),
                complete_while_typing=True,
                style=_STYLE,
                key_bindings=kb,
            )
        except Exception:
            return None

    def read(session, toolbar, placeholder=None):
        """Read one line with history + autocomplete + bottom toolbar + ghost text.
        Raises KeyboardInterrupt on Ctrl-C and EOFError on Ctrl-D (like input())."""
        ph = None
        if placeholder:
            safe = placeholder.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            ph = HTML(f'<style fg="#7a7570">{safe}</style>')
        return session.prompt(
            HTML("\n<prompt>› </prompt>"), bottom_toolbar=toolbar, placeholder=ph
        )

else:  # pragma: no cover - exercised only when prompt_toolkit is absent

    def build_session(history_path, on_shift_tab=None, on_help=None):
        return None

    def read(session, toolbar, placeholder=None):  # never called (session is None)
        raise RuntimeError("prompt_toolkit not available")
