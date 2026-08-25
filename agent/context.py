"""Context management (M4/§6) — keep the window lean.

Two pieces we actually need for a working agent:
  * budget()          truncate oversized tool results
  * drop_stale_cot()  drop old chain-of-thought between user turns
(Compaction/summarization is a later add — see build-plan.md §6.4.)
"""

from . import config
from .harmony_codec import msg_fields


def budget(text, cap=None) -> str:
    """Truncate a tool result so a huge grep/read can't flood the context window."""
    cap = cap or config.TOOL_RESULT_CAP
    if text is None:
        return ""
    if len(text) <= cap:
        return text
    return (
        text[:cap]
        + f"\n\n…[truncated {len(text) - cap} chars — narrow your query or request a line range]"
    )


def drop_stale_cot(history):
    """Remove analysis-channel (chain-of-thought) messages from prior turns.

    Called once at the start of each NEW user turn. Within a single turn's
    tool-call chain we KEEP analysis, because the model needs its own reasoning
    as context to continue after tool results (the tool-call exception).
    """
    return [m for m in history if msg_fields(m).get("channel") != "analysis"]
