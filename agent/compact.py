"""Conversation compaction (M5) — when a session gets long, summarize the older
turns into one compact note and keep only that summary plus the most recent
messages. This is the proactive complement to the reactive context-overflow
recovery in the loop: it keeps long, multi-question sessions from ever hitting
the wall, and preserves what was found (files, symbols, findings)."""

from . import config, inference
from . import harmony_codec as hc

SUMMARIZER_INSTRUCTIONS = (
    "You compress a coding assistant's exploration transcript into a concise summary that can "
    "replace the earlier turns. Keep concrete file paths, symbol names, line numbers, and "
    "findings. Drop chit-chat and reasoning. Output plain text only."
)


def _serialize(messages):
    """Flatten non-analysis messages into a readable transcript for summarizing."""
    lines = []
    for m in messages:
        f = hc.msg_fields(m)
        ch = f.get("channel")
        if ch == "analysis":
            continue  # reasoning is dropped, not summarized
        # role may be a harmony Role enum (e.g. Role.USER) — normalize to "user".
        role = str(f.get("role") or "?").rsplit(".", 1)[-1].lower()
        tag = f"{role}/{ch}" if ch else role
        rec = f" -> {f['recipient']}" if f.get("recipient") else ""
        lines.append(f"[{tag}{rec}] {f.get('content', '')}")
    return "\n".join(lines)


def compact_history(history, keep_recent=None, reasoning="low"):
    """Return a compacted copy of `history`: a single summary message followed by
    the last `keep_recent` messages. Best-effort — on any failure the original
    history is returned unchanged (the loop's overflow recovery is the backstop)."""
    keep_recent = keep_recent or config.COMPACT_KEEP_RECENT
    if len(history) <= keep_recent + 2:
        return history

    head = history[:-keep_recent]
    tail = history[-keep_recent:]
    transcript = _serialize(head)
    if not transcript.strip():
        return history

    ask = (
        "Summarize the exploration so far so it can replace the earlier turns. Include the user's "
        "goal, files/symbols already found and their locations, key findings, and what is still "
        "needed:\n\n" + transcript
    )
    prefill, _stop = hc.render(
        [hc.user_message(ask)],
        tools=None,
        reasoning=reasoning,
        instructions=SUMMARIZER_INSTRUCTIONS,
    )
    try:
        tokens, _raw = inference.complete(prefill, max_tokens=1024)
    except inference.InferenceError:
        return history  # keep the original; the loop will still try to proceed

    summary = "".join(
        f["content"]
        for f in map(hc.msg_fields, hc.parse(tokens))
        if f.get("channel") == "final"
    ).strip()
    if not summary:
        return history

    summary_msg = hc.user_message(
        "[Summary of earlier exploration in this session]\n" + summary
    )
    return [summary_msg] + tail
