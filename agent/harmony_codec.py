"""Harmony codec (M1) — you render the prompt and parse the response yourself.

Thin wrappers over the official `openai-harmony` library so the rest of the
agent never touches raw special tokens. The model only ever sees token IDs we
produced here, and we turn its output token IDs back into channel messages here.
"""

import hashlib
import os
import re
import shutil

from openai_harmony import (
    Author,
    Conversation,
    DeveloperContent,
    HarmonyEncodingName,
    HarmonyError,
    Message,
    Role,
    SystemContent,
    ReasoningEffort,
    StreamableParser,
    ToolDescription,
    load_harmony_encoding,
)

from . import config


class ParseError(RuntimeError):
    """The model's output couldn't be parsed even leniently (no salvageable
    messages). The loop treats this as a recoverable empty turn, not a crash."""


# Count of completions we had to salvage with the lenient parser because the
# strict Harmony parser rejected a malformed header (e.g. gpt-oss emitting a
# duplicated `to=functions.read`). The CLI reads this to show a [recover] note.
SALVAGE_COUNT = 0


def salvage_count() -> int:
    return SALVAGE_COUNT

# --- offline Harmony vocab --------------------------------------------------
# openai_harmony (via its Rust tiktoken engine) needs the o200k_base BPE vocab
# (~3.6MB) to map text <-> token IDs. By default it DOWNLOADS that file on first
# use and caches it in a temp dir — which fails on an offline box and disappears
# on reboot. Instead we keep the vocab IN-REPO under vendor/tiktoken/ and point
# tiktoken's cache at it, so the tokenizer loads locally and never hits the network.
# Download the file once (see vendor/tiktoken/README.md); no internet after that.
_VOCAB_URL = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
_VOCAB_SHA256 = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
_VOCAB_SIZE = 3613922
_VOCAB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor", "tiktoken"
)
_VOCAB_FILE = os.path.join(_VOCAB_DIR, "o200k_base.tiktoken")  # human-friendly name
_CACHE_NAME = hashlib.sha1(_VOCAB_URL.encode()).hexdigest()  # name tiktoken-rs looks for
_CACHE_FILE = os.path.join(_VOCAB_DIR, _CACHE_NAME)


def _prepare_offline_vocab():
    """Point tiktoken-rs at the in-repo vocab so no download is needed. Accepts the
    file under its natural name (o200k_base.tiktoken) and materializes the hashed
    cache name the engine expects. Returns None on success, else a short reason so
    the caller can show clear guidance instead of tiktoken's opaque download error.
    Respects a user-set TIKTOKEN_RS_CACHE_DIR."""
    if os.environ.get("TIKTOKEN_RS_CACHE_DIR"):
        return None  # user manages their own cache — don't override it

    src = _CACHE_FILE if os.path.exists(_CACHE_FILE) else _VOCAB_FILE
    if not os.path.exists(src):
        return "vocab file not found"

    # Verify integrity: a wrong or CRLF-mangled file fails tiktoken's own hash check
    # with an opaque error, so catch it here with an actionable one.
    digest = hashlib.sha256(open(src, "rb").read()).hexdigest()
    if digest != _VOCAB_SHA256:
        return f"vocab file is corrupted (sha256 {digest[:12]}… != expected)"

    if not os.path.exists(_CACHE_FILE):  # give the engine the hashed name it wants
        shutil.copyfile(_VOCAB_FILE, _CACHE_FILE)
    os.environ["TIKTOKEN_RS_CACHE_DIR"] = _VOCAB_DIR
    return None


def _load_encoding():
    problem = _prepare_offline_vocab()
    try:
        return load_harmony_encoding(HarmonyEncodingName.HARMONY_GPT_OSS)
    except HarmonyError as e:
        raise RuntimeError(
            f"Harmony tokenizer vocab unavailable ({problem or 'offline, not cached'}).\n\n"
            "This project runs fully offline — download the ~3.6MB BPE vocab ONCE:\n"
            f"  1. Download : {_VOCAB_URL}\n"
            f"  2. Save as  : {_VOCAB_FILE}\n"
            f"               (expected size {_VOCAB_SIZE} bytes, sha256 {_VOCAB_SHA256})\n"
            "  3. Re-run — it loads locally from then on, no internet needed.\n"
            "  See vendor/tiktoken/README.md for one-line download commands."
        ) from e


# Loaded once, from the in-repo vocab (no download).
_ENC = _load_encoding()

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


# One message block in a decoded completion: everything between <|channel|> and
# <|message|> is the header; the body runs to the next message/stop marker.
_MSG_RE = re.compile(
    r"<\|channel\|>(?P<hdr>.*?)<\|message\|>(?P<body>.*?)"
    r"(?=<\|end\|>|<\|call\|>|<\|return\|>|<\|start\|>|\Z)",
    re.DOTALL,
)
_CHANNEL_RE = re.compile(r"^\s*([A-Za-z_]+)")
_RECIPIENT_RE = re.compile(r"to=([A-Za-z0-9_.\-]+)")
_CONSTRAIN_RE = re.compile(r"<\|constrain\|>\w+")


def _lenient_parse(output_token_ids):
    """Fallback when the strict Harmony parser rejects a malformed header.

    gpt-oss-20b occasionally emits a corrupted tool-call header — most commonly
    a duplicated recipient (`to=functions.read to=functions.read`), which the
    official parser refuses whole (strict OR non-strict). We decode the tokens
    back to text and re-extract channel / recipient / body with tolerant regexes
    (taking the FIRST `to=` and dropping stray `<|constrain|>` junk), then rebuild
    real Message objects so history still renders on the next turn. Returns a
    (possibly empty) list of Messages — the caller decides what to do if empty.
    """
    text = _ENC.decode(output_token_ids)
    msgs = []
    for m in _MSG_RE.finditer(text):
        hdr, body = m.group("hdr"), m.group("body")
        ch = _CHANNEL_RE.search(hdr)
        channel = ch.group(1) if ch else None
        to = _RECIPIENT_RE.search(hdr)  # first occurrence only -> dedups
        recipient = to.group(1) if to else None
        body = _CONSTRAIN_RE.sub("", body).strip()
        msg = Message.from_role_and_content(Role.ASSISTANT, body)
        if channel:
            msg = msg.with_channel(channel)
        if recipient:
            msg = msg.with_recipient(recipient)
        msgs.append(msg)
    return msgs


def parse(output_token_ids):
    """Raw output token IDs -> list[Message] split across channels.

    Tries the strict official parser first; if it rejects a malformed header,
    falls back to a lenient regex parse so a single bad completion doesn't crash
    the agent. Raises ParseError only if nothing at all can be salvaged."""
    global SALVAGE_COUNT
    try:
        return _ENC.parse_messages_from_completion_tokens(
            output_token_ids, Role.ASSISTANT
        )
    except HarmonyError as e:
        salvaged = _lenient_parse(output_token_ids)
        if salvaged:
            SALVAGE_COUNT += 1
            return salvaged
        raise ParseError(str(e)) from e


class StreamDecoder:
    """Incremental Harmony decoder for streaming completions. Push output token
    IDs one at a time; each push returns (current_channel, text_delta) so a UI can
    render the answer as it types and show reasoning live. The authoritative parse
    at the end of the turn still uses parse() on the full token list (which keeps
    the malformed-header salvage), so this is purely for live display."""

    def __init__(self):
        self._sp = StreamableParser(_ENC, role=Role.ASSISTANT)

    def push(self, token_id):
        self._sp.process(token_id)
        return self._sp.current_channel, (self._sp.last_content_delta or "")


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
