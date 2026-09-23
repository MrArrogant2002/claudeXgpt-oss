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


def _try_json(s):
    if not (s.startswith("{") and s.endswith("}")):
        return None
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) and obj else None


def _extract_json_obj(content):
    """Return a JSON object from `content` — either the whole (possibly fenced) string,
    OR one embedded at the end of a reasoning sentence (gpt-oss writes
    `…let's read it.{"path":"x"}`). Returns the dict, else None."""
    s = (content or "").strip()
    if s.startswith("```"):
        s = s.strip("`").strip()
        if s[:4].lower() == "json":
            s = s[4:].strip()
    obj = _try_json(s)
    if obj is not None:
        return obj
    # embedded object: scan from the first '{' up to the last '}'.
    end = s.rfind("}")
    if end != -1:
        for i, ch in enumerate(s):
            if ch == "{":
                obj = _try_json(s[i : end + 1])
                if obj is not None:
                    return obj
    return None


def _infer_leaked_call(content, registry):
    """If `content` carries tool-argument JSON, return (tool_name, args) for an
    unambiguously identified tool, else None. Recovers a turn where the model wrote a
    tool call as reasoning text (no recipient) instead of a real tool call. Covers the
    read/grep/glob funnel plus bash / edit / write / multi_edit."""
    args = _extract_json_obj(content)
    if args is None:
        return None
    keys = set(args)
    has = registry.get
    if keys & _READ_RANGE_KEYS and "path" in args and has("read"):
        return "read", args
    if "command" in keys and has("bash"):
        return "bash", args
    if "edits" in keys and "path" in keys and has("multi_edit"):
        return "multi_edit", args
    if "content" in keys and "path" in keys and has("write"):
        return "write", args
    if {"old_string", "new_string"} <= keys and "path" in keys and has("edit"):
        return "edit", args
    if {"pattern", "limit"} <= keys and not (keys & _GREP_ONLY_KEYS) and has("glob"):
        return "glob", args
    if (keys & _GREP_ONLY_KEYS or "pattern" in keys or "query" in keys) and has("grep"):
        a = dict(args)
        if "pattern" not in a and "query" in a:  # model used the wrong param name
            a["pattern"] = a.pop("query")
        if "pattern" in a:
            return "grep", a
    return None


def _run_tool_call(registry, name, args, sandbox, can_use_tool, on_event):
    """Run one tool call through the permission gate. Shared by the normal tool-call
    path and the leaked-call recovery, so mutating tools are ALWAYS gated. Errors and
    permission denials come back as data (a string), never as an exception."""
    tool = registry.get(name)
    if tool is None:
        return f"ERROR: unknown tool '{name}'"
    if can_use_tool is not None and getattr(tool, "check_permissions", None):
        decision = can_use_tool(tool, args, sandbox)
        if decision is not None and getattr(decision, "behavior", "allow") == "deny":
            if on_event:
                on_event({
                    "role": "system", "channel": None, "recipient": None,
                    "content": f"[permission] denied {name}: {getattr(decision, 'reason', '')}",
                })
            return f"Permission denied: {getattr(decision, 'reason', '') or 'not allowed'}"
    try:
        return tool.run(args, sandbox)
    except Exception as e:  # errors are DATA, not crashes
        return f"ERROR: {type(e).__name__}: {e}"

# Terse on purpose: the model is instruction-tuned. A short prompt also frees context
# on a small window. Note it does NOT force tool use — the model decides when to search.
DEFAULT_INSTRUCTIONS = (
    "You are a code agent working inside a repository. Check the code with the tools "
    "(list_dir, glob, grep, read) before stating facts about it, and cite paths. "
    "When a question is about you, or cannot be answered from the repo, answer directly "
    "without searching. Reply in short plain text; do not modify files unless asked."
)

# Appended only when the matching tools are registered, so we never name a tool the
# model doesn't have. Kept terse.
EXEC_INSTRUCTIONS = (
    " You can run shell commands with `bash` (build/lint/test, then read the errors); "
    "checks only — no installs or network."
)

EDIT_INSTRUCTIONS = (
    " You can change files with `edit`/`write`/`multi_edit` (read a file before editing it); "
    "edits need permission and may be denied."
)

# Bound how many CONSECUTIVE empty-final turns we tolerate before giving up. The
# counter resets whenever the model makes a tool call (real progress), so a long
# multi-file exploration with the occasional narration turn won't trip it.
MAX_EMPTY_RECOVERY = 3
# Once the model has taken this many tool steps in a turn, an empty final means it is
# thrashing (gathered enough but won't commit), so we force synthesis instead of
# nudging for yet more tool calls — the fix for the observed 18-call spirals.
SYNTH_AFTER_STEPS = 4


def _synthesize_final(history, reasoning, instructions, on_event, cancel):
    """Last-resort recovery for gpt-oss's 'analysis-only, empty final' turns: force a
    TOOLLESS final answer. Re-rendering the SAME history with tools=None removes the
    model's option to keep deferring into 'let me explore a bit more', so it writes the
    answer now from what history already holds. This is the fix for a run that spins on
    `[recover] empty final (truncated=False)` and then gives up with no answer.

    Returns the final text, or '' if it still produced none."""
    if cancel is not None and cancel.is_set():
        return ""
    hist = list(history)
    hist.append(
        hc.user_message(
            "Stop exploring — no tools are available now. Using ONLY the information "
            "already gathered above, write the COMPLETE final answer immediately. If the "
            "task was to produce a document, output the entire document and nothing else."
        )
    )
    prefill_ids, stop_ids = hc.render(
        hist, tools=None, reasoning=reasoning, instructions=instructions
    )
    try:
        out_tokens, _ = inference.complete(
            prefill_ids, stop_ids=stop_ids, max_tokens=config.MAX_TOKENS_CAP
        )
        msgs = hc.parse(out_tokens)
    except Exception:
        return ""
    fields = [hc.msg_fields(m) for m in msgs]
    if on_event:
        for f in fields:
            on_event(f)
    answer = "".join(f["content"] for f in fields if f["channel"] == "final").strip()
    if answer:
        return answer
    # Still no final channel: gpt-oss occasionally leaves the whole answer in analysis.
    # Returning the longest analysis message beats returning nothing at all.
    analyses = [
        f["content"].strip()
        for f in fields
        if f["channel"] == "analysis" and f["content"].strip()
    ]
    return max(analyses, key=len) if analyses else ""


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


def _stream_completion(prefill_ids, max_tokens, cancel, on_delta):
    """Consume a streaming completion, emitting live channel deltas via on_delta.
    Returns (out_token_ids, final_raw_dict). The full token list is still parsed
    authoritatively by the caller (with salvage); deltas here are for display."""
    dec = hc.StreamDecoder()
    out = []
    gen = inference.complete_stream(prefill_ids, max_tokens=max_tokens, cancel=cancel)
    while True:
        try:
            tok = next(gen)
        except StopIteration as e:
            return out, (e.value or {})
        out.append(tok)
        channel, delta = dec.push(tok)
        if delta and on_delta:
            on_delta(channel, delta)


@dataclass
class Result:
    reason: str  # completed | model_error | max_turns | no_answer | cancelled
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
    cancel=None,
    stream=False,
    on_delta=None,
    can_use_tool=None,
    load_mind=True,
):
    """Run one user turn to completion. Returns (Result, updated_history).

    `on_event(fields_dict)` is called for each parsed message and each tool
    result, so a UI can show progress. `fields_dict` has role/channel/recipient/content.
    `cancel` is an optional threading.Event; when set, the loop stops at the next
    step boundary and returns Result("cancelled", ...) (a UI can wire it to Esc/Ctrl-C).
    When `stream=True`, completions stream token-by-token and `on_delta(channel, text)`
    is called with live text deltas (the answer types out; reasoning shows live), and
    `cancel` aborts generation mid-stream. Falls back to non-streaming if the server
    doesn't support it.
    """
    max_turns = max_turns or config.MAX_TURNS
    instructions = instructions or DEFAULT_INSTRUCTIONS
    if registry.get("bash"):  # execution enabled -> teach the model to use it
        instructions = instructions + EXEC_INSTRUCTIONS
    if registry.get("edit"):  # write tier enabled -> teach the model to use it
        instructions = instructions + EDIT_INSTRUCTIONS

    # Auto-load the project map (local_mind.md), like Claude Code loads CLAUDE.md, so
    # every query starts oriented. Skipped during `local init` itself (load_mind=False)
    # so we don't feed a stale map back into the run that regenerates it.
    if load_mind and config.USE_LOCAL_MIND:
        try:
            from . import project_mind

            mind = project_mind.mind_context(getattr(sandbox, "root", None))
        except Exception:
            mind = ""
        if mind:
            instructions = instructions + (
                "\n\nProject map (local_mind.md; verify specifics with tools as needed):\n"
                + mind
            )

    # New user turn: drop stale chain-of-thought from prior turns, then add input.
    history = context.drop_stale_cot(history)
    history.append(hc.user_message(user_text))

    tools = registry.harmony_tools()

    max_tokens = config.MAX_TOKENS  # may escalate if the model gets cut off
    empty_recovery = 0  # bounds nudges on empty final answers
    overflow_recovery = 0  # bounds context-overflow retries
    tool_steps = 0  # tool calls executed this turn (gates the forced-synthesis path)

    ctx = context_tokens or config.CONTEXT_TOKENS
    compact_threshold = int(ctx * config.COMPACT_RATIO)

    for turn in range(1, max_turns + 1):
        if cancel is not None and cancel.is_set():
            return Result("cancelled", "", turn - 1), history
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
            if stream:
                out_tokens, raw = _stream_completion(
                    prefill_ids, max_tokens, cancel, on_delta
                )
            else:
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
            if stream:
                # Streaming failed (e.g. server lacks stream+return_tokens). Fall
                # back to non-streaming for the rest of the turn and retry this step.
                stream = False
                if on_event:
                    on_event(
                        {
                            "role": "system",
                            "channel": None,
                            "recipient": None,
                            "content": "[stream] unavailable -> non-streaming",
                        }
                    )
                continue
            return Result("model_error", str(e), turn), history

        # Cancelled mid-stream (Esc/Ctrl-C): discard the partial output and stop.
        if cancel is not None and cancel.is_set():
            return Result("cancelled", "", turn), history

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
                try:
                    args = json.loads(call["content"]) if call["content"] else {}
                except json.JSONDecodeError as e:
                    result = f"ERROR: invalid JSON arguments: {e}"
                else:
                    result = _run_tool_call(registry, name, args, sandbox, can_use_tool, on_event)
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
            tool_steps += len(tool_calls)
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
            result = context.budget(
                _run_tool_call(registry, name, args, sandbox, can_use_tool, on_event)
            )
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
            tool_steps += 1
            _push_final_if_near_limit(history, turn, max_turns, on_event)
            continue

        # Empty final: the model reasoned but didn't commit to an answer or a tool call.
        # If it has already gathered enough (several tool steps) or repeatedly stalled,
        # force a single TOOLLESS synthesis instead of nudging it into more tool calls —
        # nudging-for-more-tools is what produced the observed 18-call spirals.
        if empty_recovery >= MAX_EMPTY_RECOVERY or tool_steps >= SYNTH_AFTER_STEPS:
            answer = _synthesize_final(history, reasoning, instructions, on_event, cancel)
            if answer:
                if on_event:
                    on_event({
                        "role": "system", "channel": None, "recipient": None,
                        "content": "[recover] tool-less synthesis -> final answer",
                    })
                return Result("completed", answer, turn), history
            return Result("no_answer", "", turn), history
        empty_recovery += 1

        # Drop this turn's (possibly truncated / huge) reasoning to free budget.
        # Tool results stay in history, so what it already found is preserved.
        history = context.drop_stale_cot(history)
        truncated = inference.hit_output_limit(raw)
        if truncated:
            max_tokens = min(max_tokens * 2, config.MAX_TOKENS_CAP)
            nudge = (
                "Your previous reply was cut off. Continue: call a tool if you still need "
                "code, otherwise write the final answer in plain text."
            )
        else:
            nudge = (
                "You haven't answered yet. Either call one tool to get what you need, or "
                "write the final answer now in plain text."
            )
        history.append(hc.user_message(nudge))
        if on_event:
            on_event(
                {
                    "role": "system",
                    "channel": None,
                    "recipient": None,
                    "content": f"[recover] empty final -> nudging "
                    f"(truncated={truncated}, steps={tool_steps})",
                }
            )

    # Ran out of tool-loop steps without a final answer — try one tool-less synthesis
    # from everything gathered before reporting failure.
    answer = _synthesize_final(history, reasoning, instructions, on_event, cancel)
    if answer:
        if on_event:
            on_event({
                "role": "system", "channel": None, "recipient": None,
                "content": "[recover] hit max turns -> tool-less synthesis produced an answer",
            })
        return Result("completed", answer, max_turns), history
    return Result("max_turns", "", max_turns), history
