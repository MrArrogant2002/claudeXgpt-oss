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

    HAS_PTK = True
except Exception:  # prompt_toolkit not installed
    HAS_PTK = False


# Slash commands offered by autocomplete (label -> one-line meta).
COMMANDS = {
    "/help": "show help",
    "/reasoning": "set reasoning effort: low | medium | high",
    "/show-reasoning": "toggle showing the model's thinking",
    "/exec": "enable/disable the bash (run code) tool: on | off",
    "/tokens": "show session token usage",
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
            "prompt": "#f5b942 bold",  # amber
            "bottom-toolbar": "#9ca3af bg:#1b1f27",
        }
    )

    def build_session(history_path):
        """Return a configured PromptSession, or None on failure."""
        try:
            return PromptSession(
                history=FileHistory(history_path),
                completer=_SlashCompleter(),
                complete_while_typing=True,
                style=_STYLE,
                key_bindings=KeyBindings(),
            )
        except Exception:
            return None

    def read(session, toolbar):
        """Read one line with history + autocomplete + bottom toolbar.
        Raises KeyboardInterrupt on Ctrl-C and EOFError on Ctrl-D (like input())."""
        return session.prompt(HTML("\n<prompt>› </prompt>"), bottom_toolbar=toolbar)

else:  # pragma: no cover - exercised only when prompt_toolkit is absent

    def build_session(history_path):
        return None

    def read(session, toolbar):  # never called (session is None)
        raise RuntimeError("prompt_toolkit not available")
