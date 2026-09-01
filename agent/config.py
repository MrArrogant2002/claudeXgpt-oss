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
# gpt-oss's own examples use temperature=1.0, but a code agent wants reliable
# tool-call formatting and reproducible runs, so we default lower. 0.3 markedly
# reduces malformed tool-call headers (duplicated recipients) and tool calls that
# leak into the reasoning channel. Raise it (e.g. 0.7) if you want more varied
# final-answer prose.
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0.3"))
REQUEST_TIMEOUT = float(os.environ.get("AGENT_TIMEOUT", "600"))
MAX_TURNS = int(os.environ.get("AGENT_MAX_TURNS", "25"))

# --- tools / sandbox --------------------------------------------------------
# The project root the tools are allowed to touch. Default = current dir.
PROJECT_ROOT = os.environ.get("AGENT_PROJECT_ROOT", os.getcwd())
# Per-tool result cap (characters) — keeps a huge grep/read from flooding context.
TOOL_RESULT_CAP = int(os.environ.get("AGENT_TOOL_RESULT_CAP", "12000"))
# Default number of lines `read` returns when no end line is given, so a bare
# read of a 2000-line file can't blow the context window. The model can paginate.
READ_DEFAULT_LINES = int(os.environ.get("AGENT_READ_DEFAULT_LINES", "300"))

# --- code execution (opt-in, off by default) --------------------------------
# The `bash` tool runs arbitrary shell commands with YOUR user's privileges so
# the agent can compile / lint / test the code and find real errors. It is a
# real risk: there is no container here, so a hostile repo's build script or
# conftest.py runs as you. Disabled unless turned on (CLI --allow-exec, or
# AGENT_ALLOW_EXEC=1). Only enable it for code you are willing to run.
ALLOW_EXEC = os.environ.get("AGENT_ALLOW_EXEC", "") not in ("", "0", "false", "False")
EXEC_TIMEOUT = int(os.environ.get("AGENT_EXEC_TIMEOUT", "60"))  # default per command (s)
EXEC_TIMEOUT_MAX = int(os.environ.get("AGENT_EXEC_TIMEOUT_MAX", "300"))  # hard cap (s)

# --- long-session compaction (M5) ------------------------------------------
# Server context window in tokens. Auto-detected from /props at startup when
# possible; this is the fallback if detection fails (matches the recommended
# `llama-server -c 32768`).
CONTEXT_TOKENS = int(os.environ.get("AGENT_CONTEXT_TOKENS", "32768"))
# Compact older turns once the rendered prompt exceeds this fraction of the window.
COMPACT_RATIO = float(os.environ.get("AGENT_COMPACT_RATIO", "0.75"))
# How many of the most recent messages to keep verbatim when compacting.
COMPACT_KEEP_RECENT = int(os.environ.get("AGENT_COMPACT_KEEP_RECENT", "6"))
