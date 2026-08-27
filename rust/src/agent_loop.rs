//! Orchestration loop — render Harmony -> raw completion -> parse channels ->
//! run tools (serially) / return final / recover from empty-final & overflow.
//! Mirrors the (proven) Python loop.

use openai_harmony::chat::Message;
use serde_json::Value;

use crate::harmony::Codec;
use crate::inference::InferError;
use crate::sandbox::Sandbox;
use crate::tools::Registry;
use crate::{config, context, harmony, inference};

pub const DEFAULT_INSTRUCTIONS: &str = "You are a coding assistant working inside a code \
repository. Answer questions by first investigating the code with the tools, then explaining. \
Funnel: use `glob` to locate files, `grep` to find where a symbol or behavior is defined, and \
`read` to read the specific lines. Prefer the actual IMPLEMENTATION/source files over test or \
config files when explaining how something works — read the module that DEFINES the behavior, \
not just its tests. Follow imports and references across files as needed. If your grep results \
are dominated by tests, config, or docs, refine the search to the source directory or search for \
the definition (e.g. 'def name' / 'class name'). Always finish with a clear final answer in \
plain text, grounded in the code you read.";

const MAX_EMPTY_RECOVERY: u32 = 2;

pub struct Outcome {
    pub reason: String, // completed | model_error | max_turns | no_answer
    pub answer: String,
    pub turns: u32,
}

/// Run one user turn to completion. `on_event(kind, recipient, content)` reports
/// progress: kind is a channel ("analysis"/"commentary"/"final"), or "tool"
/// (a tool result), or "system" (a recovery note).
pub fn run_turn(
    codec: &Codec,
    user_text: &str,
    history: &mut Vec<Message>,
    registry: &Registry,
    sandbox: &Sandbox,
    reasoning: &str,
    on_event: &mut dyn FnMut(&str, Option<&str>, &str),
) -> Outcome {
    // New user turn: drop stale chain-of-thought, then add the input.
    let taken = std::mem::take(history);
    *history = context::drop_stale_cot(taken);
    history.push(harmony::user_message(user_text));

    let tools = registry.harmony_tools();
    let max_turns = config::max_turns();
    let temperature = config::temperature();
    let mut max_tokens = config::max_tokens();
    let mut empty_recovery = 0u32;
    let mut overflow_recovery = 0u32;

    let mut turn = 0u32;
    while turn < max_turns {
        turn += 1;

        let (prefill, _stop) = match codec.render(history, &tools, reasoning, DEFAULT_INSTRUCTIONS) {
            Ok(x) => x,
            Err(e) => {
                return Outcome {
                    reason: "model_error".into(),
                    answer: format!("render failed: {e}"),
                    turns: turn,
                }
            }
        };

        let completion = match inference::complete(&prefill, max_tokens, temperature) {
            Ok(c) => c,
            Err(InferError::Overflow(_)) => {
                if overflow_recovery >= 1 {
                    return Outcome {
                        reason: "model_error".into(),
                        answer: "Context window exceeded even after dropping reasoning. Raise the \
                                 server context (e.g. llama-server -c 32768) or lower \
                                 AGENT_TOOL_RESULT_CAP."
                            .into(),
                        turns: turn,
                    };
                }
                overflow_recovery += 1;
                let taken = std::mem::take(history);
                *history = context::drop_stale_cot(taken);
                on_event("system", None, "[recover] context overflow -> dropped reasoning, retrying");
                continue;
            }
            Err(InferError::Other(msg)) => {
                return Outcome { reason: "model_error".into(), answer: msg, turns: turn }
            }
        };

        let msgs = match codec.parse(completion.tokens) {
            Ok(m) => m,
            Err(e) => {
                return Outcome {
                    reason: "model_error".into(),
                    answer: format!("parse failed: {e}"),
                    turns: turn,
                }
            }
        };

        // Inspect messages: collect tool calls + final text; report events.
        let mut tool_calls: Vec<(String, String)> = Vec::new(); // (recipient, args json)
        let mut final_text = String::new();
        for m in &msgs {
            let ch = harmony::channel_of(m).unwrap_or("?");
            let content = harmony::text_of(m);
            on_event(ch, harmony::recipient_of(m), &content);
            if ch == "commentary" {
                if let Some(r) = harmony::recipient_of(m) {
                    tool_calls.push((r.to_string(), content));
                }
            } else if ch == "final" {
                final_text.push_str(&content);
            }
        }
        // Keep this turn's assistant messages (incl. analysis) in history.
        for m in msgs {
            history.push(m);
        }

        // --- Tool calls: run serially and loop. ---
        if !tool_calls.is_empty() {
            for (recipient, args_str) in tool_calls {
                let name = recipient.rsplit('.').next().unwrap_or(recipient.as_str()).to_string();
                let result = match registry.get(&name) {
                    None => format!("ERROR: unknown tool '{name}'"),
                    Some(tool) => {
                        let raw = if args_str.trim().is_empty() { "{}" } else { args_str.as_str() };
                        match serde_json::from_str::<Value>(raw) {
                            Ok(a) => tool.run(&a, sandbox),
                            Err(e) => format!("ERROR: invalid JSON arguments: {e}"),
                        }
                    }
                };
                let result = context::budget(&result);
                history.push(harmony::tool_result_message(&recipient, &result));
                on_event("tool", Some(recipient.as_str()), &result);
            }
            continue;
        }

        // --- No tool calls: the model tried to finish. ---
        let answer = final_text.trim().to_string();
        if !answer.is_empty() {
            return Outcome { reason: "completed".into(), answer, turns: turn };
        }

        // Empty final: recover (bounded) instead of returning nothing.
        if empty_recovery >= MAX_EMPTY_RECOVERY {
            return Outcome { reason: "no_answer".into(), answer: String::new(), turns: turn };
        }
        empty_recovery += 1;
        let taken = std::mem::take(history);
        *history = context::drop_stale_cot(taken);
        let nudge = if completion.truncated {
            max_tokens = (max_tokens * 2).min(8192);
            "Your previous response was cut off before you gave an answer. Continue: if you still \
             need information, call a tool (read the actual implementation file, not just its \
             tests); otherwise write your final answer now in plain text."
        } else {
            "You have not produced a final answer yet. Either call a tool to gather the \
             implementation you need (prefer source files over tests/config), or write your final \
             answer now in plain text."
        };
        history.push(harmony::user_message(nudge));
        on_event(
            "system",
            None,
            &format!(
                "[recover] empty final -> nudging (truncated={}, max_tokens={})",
                completion.truncated, max_tokens
            ),
        );
    }

    Outcome { reason: "max_turns".into(), answer: String::new(), turns: max_turns }
}
