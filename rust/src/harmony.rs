//! Harmony codec — render a conversation to token IDs and parse output token IDs
//! back into channel messages, using the official `openai-harmony` crate.
//!
//! NOTE: token IDs are the crate's `Rank` type, which is a `u32` alias (from
//! tiktoken). If a future crate version changes that, adjust the `u32`s below.

use anyhow::Result;
use std::collections::HashSet;

use openai_harmony::chat::{
    Author, Content, Conversation, DeveloperContent, Message, ReasoningEffort, Role, SystemContent,
    ToolDescription,
};
use openai_harmony::{load_harmony_encoding, HarmonyEncoding, HarmonyEncodingName};

pub struct Codec {
    enc: HarmonyEncoding,
}

impl Codec {
    pub fn new() -> Result<Self> {
        // First call downloads the o200k_harmony vocab, then it's cached.
        let enc = load_harmony_encoding(HarmonyEncodingName::HarmonyGptOss)?;
        Ok(Self { enc })
    }

    /// Prepend system + developer, render the whole conversation to token IDs,
    /// primed for the assistant to continue. Also returns the stop-token set.
    pub fn render(
        &self,
        history: &[Message],
        tools: &[ToolDescription],
        reasoning: &str,
        instructions: &str,
    ) -> Result<(Vec<u32>, HashSet<u32>)> {
        let effort = match reasoning {
            "low" => ReasoningEffort::Low,
            "high" => ReasoningEffort::High,
            _ => ReasoningEffort::Medium,
        };
        let system = SystemContent::new().with_reasoning_effort(effort);
        let mut developer = DeveloperContent::new().with_instructions(instructions);
        if !tools.is_empty() {
            developer = developer.with_function_tools(tools.to_vec());
        }

        let mut msgs: Vec<Message> = Vec::with_capacity(history.len() + 2);
        msgs.push(Message::from_role_and_content(Role::System, system));
        msgs.push(Message::from_role_and_content(Role::Developer, developer));
        msgs.extend(history.iter().cloned());

        let convo = Conversation::from_messages(msgs);
        let tokens = self
            .enc
            .render_conversation_for_completion(&convo, Role::Assistant, None)?;
        let stop = self.enc.stop_tokens_for_assistant_actions()?;
        Ok((tokens, stop))
    }

    /// Output token IDs -> channel messages.
    pub fn parse(&self, tokens: Vec<u32>) -> Result<Vec<Message>> {
        Ok(self
            .enc
            .parse_messages_from_completion_tokens(tokens, Some(Role::Assistant))?)
    }
}

// --- field accessors on a parsed Message ---
pub fn channel_of(m: &Message) -> Option<&str> {
    m.channel.as_deref()
}

pub fn recipient_of(m: &Message) -> Option<&str> {
    m.recipient.as_deref()
}

pub fn text_of(m: &Message) -> String {
    let mut s = String::new();
    for c in &m.content {
        if let Content::Text(t) = c {
            s.push_str(&t.text);
        }
    }
    s
}

// --- message constructors ---
pub fn user_message(text: &str) -> Message {
    Message::from_role_and_content(Role::User, text)
}

pub fn tool_result_message(recipient: &str, content: &str) -> Message {
    Message::from_author_and_content(Author::new(Role::Tool, recipient), content)
        .with_channel("commentary")
}

pub fn tool_description(name: &str, description: &str, params: serde_json::Value) -> ToolDescription {
    ToolDescription::new(name, description, Some(params))
}
