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
# Hard ceiling the empty-final/truncation recovery may escalate n_predict to. Kept
# at least as high as MAX_TOKENS so raising AGENT_MAX_TOKENS never gets clamped DOWN
# on a truncation retry. Raise it for long high-reasoning runs (costs latency, not
# correctness); keep it well under the server context window.
MAX_TOKENS_CAP = int(os.environ.get("AGENT_MAX_TOKENS_CAP", str(max(8192, MAX_TOKENS))))

# --- sampling ---------------------------------------------------------------
# gpt-oss is quantization-aware trained and its examples run temperature=1.0,
# top_p=1.0. The single biggest sampling lever for a MoE model is the nucleus:
# we now send top_p EXPLICITLY (rather than inheriting llama.cpp's 0.95 default)
# so the two knobs are set together instead of fighting each other.
#
# We default temperature a bit below 1.0 because a *code agent* values reliable
# tool-call formatting: lower temperature measurably reduces malformed tool-call
# headers (duplicated recipients) and tool calls that leak into reasoning. The
# malformed-header salvage in harmony_codec.py is the safety net either way. If
# you want to follow gpt-oss's recommendation exactly, set AGENT_TEMPERATURE=1.0
# and AGENT_TOP_P=1.0 and A/B it on the GPU box against tool-call validity — that
# is the one knob only a live run can settle.
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0.6"))
TOP_P = float(os.environ.get("AGENT_TOP_P", "1.0"))
# Sent only when set to a non-neutral value, so we don't override the server's
# defaults unless you deliberately tune them on the run machine.
TOP_K = int(os.environ.get("AGENT_TOP_K", "0"))  # 0 = disabled (no top-k cutoff)
MIN_P = float(os.environ.get("AGENT_MIN_P", "0.0"))  # 0 = disabled
REPEAT_PENALTY = float(os.environ.get("AGENT_REPEAT_PENALTY", "1.0"))  # 1.0 = none

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

# --- write tier (opt-in, permission-gated, off by default) ------------------
# The write tools (edit/write/multi_edit) let the model CHANGE files. They are
# registered only when editing is enabled (CLI --allow-edit or AGENT_ALLOW_EDIT),
# and every mutation passes through the permission engine (agent/permissions.py).
# The default mode is fail-closed: `plan` = read-only.
ALLOW_EDIT = os.environ.get("AGENT_ALLOW_EDIT", "") not in ("", "0", "false", "False")
PERMISSION_MODE = os.environ.get("AGENT_PERMISSION_MODE", "plan")  # plan|default|acceptEdits|bypassPermissions|dontAsk
EDIT_MAX_BYTES = int(os.environ.get("AGENT_EDIT_MAX_BYTES", str(2_000_000)))  # refuse absurd writes
EDIT_BACKUP_DIRNAME = os.environ.get("AGENT_EDIT_BACKUP_DIR", ".agent-backups")  # under the project root

# --- local project memory (local_mind.md) -----------------------------------
# `local init` writes local_mind.md (the offline analog of CLAUDE.md); every query
# then injects it as project context. Toggle the injection with AGENT_LOCAL_MIND=0.
USE_LOCAL_MIND = os.environ.get("AGENT_LOCAL_MIND", "1") not in ("0", "false", "False")
MIND_CONTEXT_CAP = int(os.environ.get("AGENT_MIND_CONTEXT_CAP", "8000"))  # chars injected/turn
# "Massive change" thresholds for the stale check (whichever trips first):
MIND_STALE_FILES = int(os.environ.get("AGENT_MIND_STALE_FILES", "8"))  # abs changed source files
MIND_STALE_RATIO = float(os.environ.get("AGENT_MIND_STALE_RATIO", "0.15"))  # fraction of the tree
# Off by default: notify + let the user run /init. Set AGENT_MIND_AUTO_REFRESH=1 to
# have the TUI regenerate local_mind.md automatically when it detects a big change.
MIND_AUTO_REFRESH = os.environ.get("AGENT_MIND_AUTO_REFRESH", "") not in ("", "0", "false", "False")

# --- long-session compaction (M5) ------------------------------------------
# Server context window in tokens. Auto-detected from /props at startup when
# possible; this is the fallback if detection fails (matches the recommended
# `llama-server -c 32768`).
CONTEXT_TOKENS = int(os.environ.get("AGENT_CONTEXT_TOKENS", "32768"))
# Compact older turns once the rendered prompt exceeds this fraction of the window.
COMPACT_RATIO = float(os.environ.get("AGENT_COMPACT_RATIO", "0.75"))
# How many of the most recent messages to keep verbatim when compacting.
COMPACT_KEEP_RECENT = int(os.environ.get("AGENT_COMPACT_KEEP_RECENT", "6"))
