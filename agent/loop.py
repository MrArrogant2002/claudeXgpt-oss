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

from . import compact, config, context, inference
from . import harmony_codec as hc

# Keys that unambiguously identify a tool when the model "leaks" a tool call as
# plain JSON in the reasoning/commentary channel instead of emitting a real call.
# `pattern` is shared by grep and glob, so it is NOT distinctive on its own; the
# distinctive signals are the line-range keys (read), `limit` (glob), and
# max_matches/query/ignore_case (grep).
_READ_RANGE_KEYS = {"start_line", "end_line", "line_start", "line_end", "start", "end"}
_GREP_ONLY_KEYS = {"query", "max_matches", "ignore_case"}


def _extract_json_obj(content):
    """Return the dict if `content` is (or wraps) a single bare JSON object, else None."""
    s = content.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s[:4].lower() == "json":
            s = s[4:]
        s = s.strip()
    if not (s.startswith("{") and s.endswith("}")):
        return None
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) and obj else None


def _infer_leaked_call(content, registry):
    """If `content` is bare tool-argument JSON, return (tool_name, args) for an
    unambiguously identified tool, else None. Recovers a turn where the model wrote
    a tool call as reasoning text (no recipient) instead of a real tool call."""
    args = _extract_json_obj(content)
    if args is None:
        return None
    keys = set(args)
    if keys & _READ_RANGE_KEYS and "path" in args and registry.get("read"):
        return "read", args
    # glob: has `limit` + `pattern` and none of grep's distinctive keys.
    if {"pattern", "limit"} <= keys and not (keys & _GREP_ONLY_KEYS) and registry.get("glob"):
        return "glob", args
    # grep: a grep-distinctive key, or a bare pattern/query search.
    if (keys & _GREP_ONLY_KEYS or "pattern" in keys or "query" in keys) and registry.get("grep"):
        a = dict(args)
        if "pattern" not in a and "query" in a:  # model used the wrong param name
            a["pattern"] = a.pop("query")
        if "pattern" in a:
            return "grep", a
    return None

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
    "\n\nGROUNDING RULES (important):\n"
    "- Base every statement on what the tools actually returned. Do NOT describe a file, "
    "class, or function you have not opened or grepped.\n"
    "- Even if you recognize the project (a well-known library or framework), do NOT "
    "answer from memory — the code in THIS repository may differ from what you remember. "
    "Verify with the tools before stating anything about it.\n"
    "- When you state what a specific file/class/function does, cite it by path (and line "
    "range when useful) so the answer is verifiable.\n"
    "- For a broad 'explain the whole codebase' request, quickly ground each key module "
    "before describing it — a short `read` of its top or a `grep` of its main definitions "
    "is enough; you need not read every file in full. If you must infer something you did "
    "not verify, say so explicitly instead of presenting it as fact.\n"
    "Always finish with a clear final answer in plain text, grounded in the code you read."
)

# Bound how many CONSECUTIVE empty-final turns we tolerate before giving up. The
# counter resets whenever the model makes a tool call (real progress), so a long
# multi-file exploration with the occasional narration turn won't trip it.
MAX_EMPTY_RECOVERY = 3


def _push_final_if_near_limit(history, turn, max_turns, on_event):
    """When almost out of steps, tell the model to synthesize now instead of
    reading more. Shared by the tool-call and leaked-call recovery paths."""
    if turn < max_turns - 1:
        return
    history.append(
        hc.user_message(
            "You are almost out of exploration steps. Based on what you have "
            "already read, write your final answer now in plain text — do NOT "
            "call any more tools."
        )
    )
    if on_event:
        on_event(
            {
                "role": "system",
                "channel": None,
                "recipient": None,
                "content": "[nudge] near step limit -> asking for the final answer",
            }
        )


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

        salvage_before = hc.salvage_count()
        try:
            msgs = hc.parse(out_tokens)
        except hc.ParseError:
            # Output was unparseable even leniently. Treat like an empty final:
            # drop it and nudge for a clean response, bounded by MAX_EMPTY_RECOVERY.
            if empty_recovery >= MAX_EMPTY_RECOVERY:
                return Result("no_answer", "", turn), history
            empty_recovery += 1
            history = context.drop_stale_cot(history)
            history.append(
                hc.user_message(
                    "Your previous response could not be parsed. Respond again: either "
                    "call a single tool with valid JSON arguments, or write your final "
                    "answer in plain text."
                )
            )
            if on_event:
                on_event(
                    {
                        "role": "system",
                        "channel": None,
                        "recipient": None,
                        "content": "[recover] unparseable output -> nudging, retrying",
                    }
                )
            continue
        # gpt-oss sometimes emits a malformed tool-call header (e.g. a duplicated
        # recipient) that the strict parser rejects; hc.parse salvaged it here.
        if hc.salvage_count() > salvage_before and on_event:
            on_event(
                {
                    "role": "system",
                    "channel": None,
                    "recipient": None,
                    "content": "[recover] malformed tool-call header salvaged",
                }
            )
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
            # Made progress this turn — reset the consecutive-empty-final budget.
            empty_recovery = 0
            _push_final_if_near_limit(history, turn, max_turns, on_event)
            continue

        # --- No tool calls: the model tried to finish this turn. ---
        answer = "".join(
            f["content"] for f in fields if f["channel"] == "final"
        ).strip()
        if answer:
            return Result("completed", answer, turn), history

        # The model sometimes writes a tool call as plain JSON in the reasoning/
        # commentary channel (no recipient) instead of emitting a real call, which
        # would otherwise waste this turn. If the tool is unambiguous, run it.
        leaked = None
        for f in fields:
            if f["channel"] in ("analysis", "commentary") and not f["recipient"]:
                leaked = _infer_leaked_call(f["content"], registry)
                if leaked:
                    break
        if leaked:
            name, args = leaked
            recipient = f"functions.{name}"
            try:
                result = registry.get(name).run(args, sandbox)
            except Exception as e:  # errors are DATA, not crashes
                result = f"ERROR: {type(e).__name__}: {e}"
            result = context.budget(result)
            history.append(hc.tool_result_message(recipient, result))
            if on_event:
                on_event(
                    {
                        "role": "system",
                        "channel": None,
                        "recipient": None,
                        "content": f"[recover] tool call leaked into reasoning -> dispatched {name}",
                    }
                )
                on_event(
                    {
                        "role": "tool",
                        "channel": "commentary",
                        "recipient": recipient,
                        "content": result,
                    }
                )
            empty_recovery = 0  # progress: don't count this as an empty turn
            _push_final_if_near_limit(history, turn, max_turns, on_event)
            continue

        # Empty final: the model gave up early or got cut off mid-thought.
        # Recover instead of returning nothing — bounded to avoid infinite loops.
        if empty_recovery >= MAX_EMPTY_RECOVERY:
            return Result("no_answer", "", turn), history
        empty_recovery += 1

        truncated = inference.hit_output_limit(raw)
        # Did the model try to call a tool but botch the format (wrote the arguments
        # as prose/JSON without addressing a function)? If so, nudge specifically.
        botched_call = any(
            _extract_json_obj(f["content"]) is not None
            for f in fields
            if f["channel"] in ("analysis", "commentary")
        )
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
        elif empty_recovery >= MAX_EMPTY_RECOVERY:
            # Last chance before giving up: force synthesis, no more exploring.
            nudge = (
                "STOP exploring. You have gathered enough information. Do NOT call any more "
                "tools. Write your final answer NOW, in plain text, synthesizing what you "
                "have already read."
            )
        elif botched_call:
            # It tried to call a tool but wrote the arguments as text/reasoning.
            nudge = (
                "It looks like you wrote tool arguments as plain text instead of calling "
                "the tool. To use a tool you must emit it as a proper tool call addressed "
                "to the function (e.g. call `read` or `grep`), not describe it in your "
                "reasoning. Make the tool call now, or if you already have enough "
                "information write your final answer in plain text."
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
