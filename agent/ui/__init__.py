"""Terminal UI (TUI) for the local code agent — a Claude Code–style front-end.

Stdlib-only (raw ANSI, no third-party libraries, fully offline). The UI is a
richer renderer over the agent's existing `loop.run_turn` + `on_event` seam.
"""
