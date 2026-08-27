"""Configuration — all values overridable via environment variables so you can
change them on the run machine without editing code (handy for git push/pull)."""

import os

# --- llama.cpp server -------------------------------------------------------
# BASE_URL is the server ROOT (no /v1). The RAW completion endpoint lives at
# <BASE_URL>/completion — this is a llama.cpp-native endpoint that accepts a
# token-ID array and (with return_tokens) gives token IDs back. We deliberately
# do NOT use /v1/chat/completions, because that would apply llama.cpp's own
# Harmony template on top of the one we render ourselves.
BASE_URL = os.environ.get("AGENT_BASE_URL", "http://localhost:8081").rstrip("/")
COMPLETION_URL = BASE_URL + "/completion"
HEALTH_URL = BASE_URL + "/health"
PROPS_URL = BASE_URL + "/props"

# --- generation -------------------------------------------------------------
MODEL = os.environ.get("AGENT_MODEL", "gpt-oss-20b")  # informational only
REASONING_EFFORT = os.environ.get("AGENT_REASONING", "medium")  # low | medium | high
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "4096"))
# gpt-oss's own examples use temperature=1.0; 0.7 tends to give more reliable
# tool-call JSON for a coding agent. Tune as you like.
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0.7"))
REQUEST_TIMEOUT = float(os.environ.get("AGENT_TIMEOUT", "600"))
MAX_TURNS = int(os.environ.get("AGENT_MAX_TURNS", "12"))

# --- tools / sandbox --------------------------------------------------------
# The project root the tools are allowed to touch. Default = current dir.
PROJECT_ROOT = os.environ.get("AGENT_PROJECT_ROOT", os.getcwd())
# Per-tool result cap (characters) — keeps a huge grep/read from flooding context.
TOOL_RESULT_CAP = int(os.environ.get("AGENT_TOOL_RESULT_CAP", "12000"))
# Default number of lines `read` returns when no end line is given, so a bare
# read of a 2000-line file can't blow the context window. The model can paginate.
READ_DEFAULT_LINES = int(os.environ.get("AGENT_READ_DEFAULT_LINES", "300"))

# --- long-session compaction (M5) ------------------------------------------
# Server context window in tokens. Auto-detected from /props at startup when
# possible; this is the fallback if detection fails (matches the recommended
# `llama-server -c 32768`).
CONTEXT_TOKENS = int(os.environ.get("AGENT_CONTEXT_TOKENS", "32768"))
# Compact older turns once the rendered prompt exceeds this fraction of the window.
COMPACT_RATIO = float(os.environ.get("AGENT_COMPACT_RATIO", "0.75"))
# How many of the most recent messages to keep verbatim when compacting.
COMPACT_KEEP_RECENT = int(os.environ.get("AGENT_COMPACT_KEEP_RECENT", "6"))
