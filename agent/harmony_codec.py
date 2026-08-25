"""Harmony codec (M1) — you render the prompt and parse the response yourself.

Thin wrappers over the official `openai-harmony` library so the rest of the
agent never touches raw special tokens. The model only ever sees token IDs we
produced here, and we turn its output token IDs back into channel messages here.
"""

from openai_harmony import (
    Author,
    Conversation,
    DeveloperContent,
    HarmonyEncodingName,
    Message,
    Role,
    SystemContent,
    ReasoningEffort,
    ToolDescription,
    load_harmony_encoding,
)

from . import config

# Loaded once. First call downloads the o200k_harmony vocab, then it's cached.
_ENC = load_harmony_encoding(HarmonyEncodingName.HARMONY_GPT_OSS)

_EFFORT = {
    "low": ReasoningEffort.LOW,
    "medium": ReasoningEffort.MEDIUM,
    "high": ReasoningEffort.HIGH,
}


def encoding():
    return _ENC


# --- message constructors ---------------------------------------------------
def user_message(text: str) -> Message:
    return Message.from_role_and_content(Role.USER, text)


def tool_result_message(recipient: str, content: str) -> Message:
    """A tool's output, addressed back from the tool to the assistant.
    `recipient` is what the model called, e.g. 'functions.read'."""
    return Message.from_author_and_content(
        Author.new(Role.TOOL, recipient), content
    ).with_channel("commentary")


def tool_description(name: str, description: str, parameters: dict) -> ToolDescription:
    return ToolDescription.new(name, description, parameters=parameters)


# --- render / parse ---------------------------------------------------------
def render(
    history, tools=None, reasoning=None, instructions="You are a helpful assistant."
):
    """history: list[Message]. Returns (prefill_token_ids, stop_token_ids)."""
    reasoning = reasoning or config.REASONING_EFFORT
    system = SystemContent.new().with_reasoning_effort(_EFFORT[reasoning])
    developer = DeveloperContent.new().with_instructions(instructions)
    if tools:
        developer = developer.with_function_tools(tools)

    convo = Conversation.from_messages(
        [
            Message.from_role_and_content(Role.SYSTEM, system),
            Message.from_role_and_content(Role.DEVELOPER, developer),
            *history,
        ]
    )
    prefill_ids = _ENC.render_conversation_for_completion(convo, Role.ASSISTANT)
    stop_ids = _ENC.stop_tokens_for_assistant_actions()  # [<|return|>, <|call|>]
    return prefill_ids, stop_ids


def parse(output_token_ids):
    """Raw output token IDs -> list[Message] split across channels."""
    return _ENC.parse_messages_from_completion_tokens(output_token_ids, Role.ASSISTANT)


# --- robust field access on a parsed Message --------------------------------
def _content_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return content.get("text", "")
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, dict):
                parts.append(c.get("text", ""))
            else:
                parts.append(getattr(c, "text", ""))
        return "".join(parts)
    return getattr(content, "text", str(content))


def msg_fields(m) -> dict:
    """Normalize a parsed Message to {role, channel, recipient, content}.
    Works whether the library exposes attributes or a to_dict()."""
    d = m.to_dict() if hasattr(m, "to_dict") else {}
    role = d.get("role")
    if role is None and isinstance(d.get("author"), dict):
        role = d["author"].get("role")
    return {
        "role": role,
        "channel": d.get("channel"),
        "recipient": d.get("recipient"),
        "content": _content_text(d.get("content")),
    }
