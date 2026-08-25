"""Orchestration loop (M0-M4/§4) — the agent's center of gravity.

One loop: render Harmony -> raw completion -> parse channels ->
  * no tool calls  -> return the final answer
  * tool calls     -> run them (serially), append results, loop again
with a max_turns circuit breaker, tool errors treated as data, and stale
chain-of-thought dropped at the start of each new user turn.
"""

import json
from dataclasses import dataclass

from . import config, context, inference
from . import harmony_codec as hc

DEFAULT_INSTRUCTIONS = (
    "You are a coding assistant working inside a code repository. "
    "Before answering questions about the code, USE THE TOOLS to find the truth: "
    "use `glob` to locate files by name/pattern, `grep` to search file contents for "
    "symbols or strings, and `read` to read the specific lines that matter. "
    "Prefer narrow, targeted tool calls and follow imports/references across files. "
    "Only give your final answer once it is grounded in the actual code you read."
)


@dataclass
class Result:
    reason: str  # completed | model_error | max_turns
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

    for turn in range(1, max_turns + 1):
        prefill_ids, stop_ids = hc.render(
            history, tools=tools, reasoning=reasoning, instructions=instructions
        )
        try:
            out_tokens, _raw = inference.complete(prefill_ids, stop_ids=stop_ids)
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

        # No tool calls -> the model produced its final answer.
        if not tool_calls:
            answer = "".join(
                f["content"] for f in fields if f["channel"] == "final"
            ).strip()
            return Result("completed", answer, turn), history

        # Execute tool calls serially (Harmony may emit more than one per turn).
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

    return Result("max_turns", "", max_turns), history
