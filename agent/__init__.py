"""Local code agent — gpt-oss brain via llama.cpp, Harmony rendered by hand.

Fully local: we render the Harmony prompt ourselves and feed raw token IDs to
llama.cpp's /completion endpoint (no chat template, no cloud). See docs/ for the
design notes and docs/architecture/ for the diagrams.

Package map
-----------
Orchestration
  loop.py            one user turn: render -> infer -> parse -> dispatch -> repeat,
                     with recovery, compaction, streaming, and the permission gate
Model I/O
  harmony_codec.py   render conversation -> token IDs; parse tokens -> channels;
                     StreamDecoder for live streaming; offline vocab loader
  inference.py       llama.cpp raw /completion client (complete + complete_stream/SSE)
Context management
  context.py         per-result budgeting; drop stale chain-of-thought
  compact.py         summarize older turns when the window fills
Safety
  sandbox.py         path containment — the hard wall under every tool
  permissions.py     Claude Code-style permission engine (modes, deny rules) for writes
  edits.py           read-before-write freshness, atomic writes, backups, diffs
config.py            settings + environment overrides

Subpackages
  tools/             the tool tiers: list_dir/glob/grep/read (navigate),
                     bash (execute), edit/write/multi_edit (write) + Registry
  ui/                the TUI: app (REPL), render, theme, session, banner

Entry point (repo root): tui.py (interactive TUI)
"""

__version__ = "0.1.0"
