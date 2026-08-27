"""Orchestration loop (M0-M4/§4) — the agent's center of gravity.

One loop: render Harmony -> raw completion -> parse channels ->
  * tool calls     -> run them (serially), append results, loop again
  * final answer   -> return it
  * empty final    -> recover: drop stale reasoning, escalate tokens if the
                      output was cut off, nudge the model, retry (bounded)
with a max_turns circuit breaker, tool errors treated as data, and stale
chain-of-thought dropped at the start of each new user turn.
"""

import json
from dataclasses import dataclass

from . import config, context, inference
from . import harmony_codec as hc

DEFAULT_INSTRUCTIONS = (
    "You are a coding assistant working inside a code repository. "
    "Answer questions by first investigating the code with the tools, then explaining. "
    "Funnel (cheap to expensive): `list_dir` to orient, `glob` to locate files by name, "
    "`grep` to find where a symbol or behavior is defined, and `read` to read the specific "
    "lines. Do the minimum needed: if the question only asks WHERE something is, a glob or "
    "grep result is enough — do not read a file unless you actually need its contents. "
    "Prefer the actual IMPLEMENTATION/source files over test or config files when "
    "explaining how something works — read the module that DEFINES the behavior, not "
    "just its tests. Follow imports and references across files as needed. "
    "If your grep results are dominated by tests, config, or docs, refine the search to "
    "the source directory or search for the definition (e.g. 'def name' / 'class name'). "
    "Always finish with a clear final answer in plain text, grounded in the code you read."
)

# Bound how many times we nudge the model when it returns an empty final answer,
# so a persistently-empty model can't loop forever.
MAX_EMPTY_RECOVERY = 2


@dataclass
class Result:
    reason: str  # completed | model_error | max_turns | no_answer
    answer: str = ""
    turns: int = 0


def run_turn(
    user_text,
    history,
    registry,
    sandbox,
    *,
    reasoning=None,
    instructions=None,
    on_event=None,
    max_turns=None,
    context_tokens=None,
):
    """Run one user turn to completion. Returns (Result, updated_history).

    `on_event(fields_dict)` is called for each parsed message and each tool
    result, so a UI can show progress. `fields_dict` has role/channel/recipient/content.
    """
    max_turns = max_turns or config.MAX_TURNS
    instructions = instructions or DEFAULT_INSTRUCTIONS

    # New user turn: drop stale chain-of-thought from prior turns, then add input.
    history = context.drop_stale_cot(history)
    history.append(hc.user_message(user_text))

    tools = registry.harmony_tools()

    max_tokens = config.MAX_TOKENS  # may escalate if the model gets cut off
    empty_recovery = 0  # bounds nudges on empty final answers
    overflow_recovery = 0  # bounds context-overflow retries

    ctx = context_tokens or config.CONTEXT_TOKENS
    compact_threshold = int(ctx * config.COMPACT_RATIO)

    for turn in range(1, max_turns + 1):
        prefill_ids, stop_ids = hc.render(
            history, tools=tools, reasoning=reasoning, instructions=instructions
        )

        # Proactive compaction (M5): if the prompt is getting close to the window,
        # summarize older turns and re-render. Overflow recovery below is the backstop.
        if len(prefill_ids) > compact_threshold:
            compacted = compact.compact_history(history)
            if len(compacted) < len(history):
                history = compacted
                prefill_ids, stop_ids = hc.render(
                    history, tools=tools, reasoning=reasoning, instructions=instructions
                )
                if on_event:
                    on_event(
                        {
                            "role": "system",
                            "channel": None,
                            "recipient": None,
                            "content": f"[compact] summarized older turns "
                            f"({len(prefill_ids)} prompt tokens after compaction)",
                        }
                    )
        try:
            out_tokens, raw = inference.complete(
                prefill_ids, stop_ids=stop_ids, max_tokens=max_tokens
            )
        except inference.ContextOverflowError:
            # Prompt outgrew the server's context window. Dropping analysis is safe
            # (no tool_use/result pairing to break) and frees the most tokens.
            if overflow_recovery >= 1:
                return (
                    Result(
                        "model_error",
                        "Context window exceeded even after dropping reasoning. Raise the "
                        "server context (e.g. llama-server -c 32768), lower "
                        "AGENT_TOOL_RESULT_CAP, or ask a narrower question.",
                        turn,
                    ),
                    history,
                )
            overflow_recovery += 1
            history = context.drop_stale_cot(history)
            if on_event:
                on_event(
                    {
                        "role": "system",
                        "channel": None,
                        "recipient": None,
                        "content": "[recover] context overflow -> dropped reasoning, retrying",
                    }
                )
            continue
        except inference.InferenceError as e:
            return Result("model_error", str(e), turn), history

        msgs = hc.parse(out_tokens)
        for m in msgs:  # keep this turn's assistant messages (incl. analysis)
            history.append(m)

        fields = [hc.msg_fields(m) for m in msgs]
        if on_event:
            for f in fields:
                on_event(f)

        tool_calls = [
            f for f in fields if f["channel"] == "commentary" and f["recipient"]
        ]

        # --- Tool calls: run them serially (Harmony may emit >1) and loop. ---
        if tool_calls:
            for call in tool_calls:
                recipient = call["recipient"]  # e.g. "functions.read"
                name = recipient.split(".")[-1]
                tool = registry.get(name)
                if tool is None:
                    result = f"ERROR: unknown tool '{name}'"
                else:
                    try:
                        args = json.loads(call["content"]) if call["content"] else {}
                    except json.JSONDecodeError as e:
                        result = f"ERROR: invalid JSON arguments: {e}"
                    else:
                        try:
                            result = tool.run(args, sandbox)
                        except Exception as e:  # errors are DATA, not crashes
                            result = f"ERROR: {type(e).__name__}: {e}"
                result = context.budget(result)
                history.append(hc.tool_result_message(recipient, result))
                if on_event:
                    on_event(
                        {
                            "role": "tool",
                            "channel": "commentary",
                            "recipient": recipient,
                            "content": result,
                        }
                    )
            continue

        # --- No tool calls: the model tried to finish this turn. ---
        answer = "".join(
            f["content"] for f in fields if f["channel"] == "final"
        ).strip()
        if answer:
            return Result("completed", answer, turn), history

        # Empty final: the model gave up early or got cut off mid-thought.
        # Recover instead of returning nothing — bounded to avoid infinite loops.
        if empty_recovery >= MAX_EMPTY_RECOVERY:
            return Result("no_answer", "", turn), history
        empty_recovery += 1

        truncated = inference.hit_output_limit(raw)
        # Drop this turn's (possibly truncated / huge) reasoning to free budget.
        # Tool results stay in history, so what it already found is preserved.
        history = context.drop_stale_cot(history)
        if truncated:
            max_tokens = min(max_tokens * 2, 8192)
            nudge = (
                "Your previous response was cut off before you gave an answer. "
                "Continue: if you still need information, call a tool (read the actual "
                "implementation file, not just its tests); otherwise write your final "
                "answer now in plain text."
            )
        else:
            nudge = (
                "You have not produced a final answer yet. Either call a tool to gather "
                "the implementation you need (prefer source files over tests/config), or "
                "write your final answer now in plain text."
            )
        history.append(hc.user_message(nudge))
        if on_event:
            on_event(
                {
                    "role": "system",
                    "channel": None,
                    "recipient": None,
                    "content": f"[recover] empty final -> nudging "
                    f"(truncated={truncated}, max_tokens={max_tokens})",
                }
            )

    return Result("max_turns", "", max_turns), history
