//! Context management — truncate oversized tool results and drop stale
//! chain-of-thought between user turns (mirrors the Python agent).

use openai_harmony::chat::{Message, Role};

use crate::config;

/// Truncate a tool result so a huge grep/read can't flood the context window.
/// UTF-8 safe.
pub fn budget(text: &str) -> String {
    let cap = config::tool_result_cap();
    if text.len() <= cap {
        return text.to_string();
    }
    let mut end = cap;
    while end > 0 && !text.is_char_boundary(end) {
        end -= 1;
    }
    format!(
        "{}\n\n… [truncated {} chars — narrow your query or request a line range]",
        &text[..end],
        text.len() - end
    )
}

/// Drop analysis-channel (chain-of-thought) messages. Called at the start of each
/// new user turn and during recovery; tool results and finals are preserved.
pub fn drop_stale_cot(history: Vec<Message>) -> Vec<Message> {
    history
        .into_iter()
        .filter(|m| !(m.author.role == Role::Assistant && m.channel.as_deref() == Some("analysis")))
        .collect()
}
