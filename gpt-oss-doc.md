# gpt-oss — Raw Local Inference (You Own the Harmony Format)

A practical, self-contained guide to running OpenAI's **gpt-oss** open-weight models **at the token level**: you construct the **Harmony** prompt yourself, feed **raw token IDs** to the raw model, and parse the **raw token IDs** it returns. No chat template, no OpenAI-compatible server, no runtime that "defaultly" converts your input to Harmony behind your back. No Ollama, no llama.cpp.

> Sourced from the OpenAI Cookbook gpt-oss articles:
> - [Harmony response format](https://developers.openai.com/cookbook/articles/openai-harmony)
> - [Verifying implementations](https://developers.openai.com/cookbook/articles/gpt-oss/verifying-implementations)
> - [Handling raw CoT](https://developers.openai.com/cookbook/articles/gpt-oss/handle-raw-cot)
> - [Fine-tuning with Transformers](https://developers.openai.com/cookbook/articles/gpt-oss/fine-tune-transfomers)
> - [gpt-oss topic index](https://developers.openai.com/cookbook/topic/gpt-oss)
>
> Where a code block combines documented pieces that don't appear together in a single cookbook page, it is marked **[synthesized]** — verify method names against your installed library versions.

---

## Table of Contents

1. [What gpt-oss is](#1-what-gpt-oss-is)
2. [The approach: you own Harmony, the model stays raw](#2-the-approach-you-own-harmony-the-model-stays-raw)
3. [What each source article covers](#3-what-each-source-article-covers)
4. [Hardware requirements](#4-hardware-requirements)
5. [Raw inference with Transformers (no chat template)](#5-raw-inference-with-transformers-no-chat-template)
6. [Raw inference with vLLM offline (direct token sampling)](#6-raw-inference-with-vllm-offline-direct-token-sampling)
7. [Raw inference with the OpenAI reference implementation](#7-raw-inference-with-the-openai-reference-implementation)
8. [The Harmony format (spec)](#8-the-harmony-format-spec)
9. [Rendering Harmony yourself](#9-rendering-harmony-yourself)
10. [Parsing the response yourself](#10-parsing-the-response-yourself)
11. [Raw chain-of-thought (CoT) handling](#11-raw-chain-of-thought-cot-handling)
12. [Tool calling — the manual loop](#12-tool-calling--the-manual-loop)
13. [Built-in tools, structured output, preambles](#13-built-in-tools-structured-output-preambles)
14. [Verifying your implementation](#14-verifying-your-implementation)
15. [Fine-tuning (still a raw model)](#15-fine-tuning-still-a-raw-model)
16. [End-to-end raw recipes](#16-end-to-end-raw-recipes)
17. [Gotchas & quick reference](#17-gotchas--quick-reference)

---

## 1. What gpt-oss is

gpt-oss is OpenAI's family of **open-weight** models. Two variants:

| Model | Total params | Architecture | Default quant | Rough VRAM |
|-------|--------------|--------------|---------------|------------|
| **`openai/gpt-oss-20b`** | 20B (Mixture-of-Experts) | MoE | MXFP4 | ~16 GB |
| **`openai/gpt-oss-120b`** | 120B (Mixture-of-Experts) | MoE | MXFP4 | ≥60 GB (single H100 or multi-GPU) |

Facts that matter for a raw pipeline:

- **The model is a next-token predictor over token IDs.** It has no idea what "a chat message" is. It only continues a sequence of integers. *You* are responsible for making that sequence a valid Harmony conversation.
- **Both ship MXFP4-quantized.** MXFP4 (4-bit float) runs natively on **Hopper+/RTX 50xx** (H100, GB200, RTX 50-series). On older GPUs you must **dequantize to bf16**, which pushes the 20b to ~48 GB.
- **Harmony is mandatory.** Quoting the spec: *"gpt-oss should not be used without using the harmony format, as it will not work correctly."* The weights were trained on Harmony-structured token sequences.
- **The model emits a raw chain-of-thought** on an `analysis` channel that is **not safety-filtered** and must never be shown to users verbatim (§11).

---

## 2. The approach: you own Harmony, the model stays raw

The whole point of this document: **no abstraction sits between your data and the token IDs.** You do every step:

```
 your intent
     │  (you author messages, roles, channels)
     ▼
[1] RENDER  →  Harmony conversation  →  token IDs      ← you (openai-harmony, or by hand)
     │
     ▼
[2] GENERATE  →  raw model.generate(input_ids=…)  →  output token IDs   ← the raw model only predicts tokens
     │
     ▼
[3] PARSE  →  split token IDs into messages/channels   ← you (openai-harmony, or by hand)
     │
     ▼
 final answer (show) + analysis CoT (keep private) + tool calls (dispatch)
```

### What "raw" excludes (do NOT use these for this goal)

These are convenience layers that **auto-convert your input to Harmony**. They are exactly what you're avoiding:

| Layer | Why it's excluded |
|-------|-------------------|
| `tokenizer.apply_chat_template(messages, …)` | Renders Harmony for you from a `messages` list — hides step [1]. |
| Transformers `transformers serve` | HTTP server that applies the chat template internally. |
| vLLM **OpenAI-compatible server** (`vllm serve` + `client.chat.completions.create` / `responses.create`) | Renders and parses Harmony inside the server — hides steps [1] and [3]. |
| Ollama / llama.cpp / LM Studio | Bundled runtimes with their own templating. |

### What "raw" allows (all satisfy step [2] on the raw model)

| Runtime | How you stay raw | Section |
|---------|------------------|---------|
| **Hugging Face Transformers** | Load `AutoModelForCausalLM`, skip `apply_chat_template`, call `model.generate(input_ids=<your token IDs>)`. | §5 |
| **vLLM (offline `LLM` class)** | `LLM.generate(prompts=[{"prompt_token_ids": …}])` — no server, no auto-Harmony. | §6 |
| **OpenAI reference impl** (`gpt_oss.generate`, PyTorch/Triton) | Operates directly on tokens; the most bare-metal path. | §7 |

### On the `openai-harmony` library

`openai-harmony` is **not** an "architecture" and does **not** run the model. It is OpenAI's official **renderer/parser** — a deterministic function from *(the conversation you explicitly author)* → *token IDs*, and back. You drive every field (role, channel, recipient, content). Using it is still "generating the Harmony format yourself"; it just spares you from hand-emitting `<|start|>`/`<|channel|>`/`<|message|>` and memorizing token IDs. If you want zero libraries, §9.3 shows the fully by-hand string path.

---

## 3. What each source article covers

| Article | What it explains | Where here |
|---------|------------------|-----------|
| **OpenAI Harmony Response Format** | Roles, channels, special tokens + IDs, message layout, system/developer messages, reasoning levels, function-call syntax, built-in tools, structured output, and the `openai-harmony` renderer. | §8, §9, §10, §12, §13 |
| **Verifying implementations** | Compatibility test harness, eval benchmarks (AIME/GPQA/Healthbench), required API shapes for raw CoT, common pitfalls (Harmony mapping, CoT passthrough, MXFP4). | §14 |
| **Handle raw CoT** | What raw CoT is, why never to show it raw, analysis-channel drop rules, and reasoning-field conventions. | §11 |
| **Fine-tune with Transformers** | LoRA/PEFT fine-tune of gpt-oss-20b (dataset, MXFP4 dequantize, MoE-aware LoRA, `SFTConfig`, save, inference). | §15 |
| **gpt-oss topic index** | Catalog of gpt-oss guides and local runners. | §3 + §5–§7 |

---

## 4. Hardware requirements

| Model | MXFP4 (Hopper+/RTX 50xx) | bf16 fallback (older GPUs) | Notes |
|-------|--------------------------|----------------------------|-------|
| gpt-oss-20b | ~16 GB VRAM | ~48 GB VRAM | Fits high-end consumer GPUs when MXFP4-native |
| gpt-oss-120b | ≥60 GB VRAM | multi-GPU | Single H100 or multi-GPU (tensor/expert parallel) |

MXFP4 needs Hopper or newer (H100, GB200, RTX 50-series). Without it, expect the bf16 numbers.

---

## 5. Raw inference with Transformers (no chat template)

This is the primary raw path: a normal `AutoModelForCausalLM`, but **you never call `apply_chat_template`**. You render Harmony to token IDs yourself, feed them to `generate`, and parse the returned IDs yourself.

### 5.1 Install

```bash
pip install -U transformers accelerate torch triton==3.4 kernels
pip install openai-harmony
```

### 5.2 Load the raw model (and the Harmony encoding)

```py
import torch
from transformers import AutoModelForCausalLM
from openai_harmony import (
    load_harmony_encoding, HarmonyEncodingName,
    Conversation, Message, Role, SystemContent, DeveloperContent,
)

model_name = "openai/gpt-oss-20b"

# The raw model — a token-in / token-out next-token predictor.
model = AutoModelForCausalLM.from_pretrained(
    model_name,
    torch_dtype="auto",
    device_map="auto",
)

# The Harmony renderer/parser — driven entirely by you.
enc = load_harmony_encoding(HarmonyEncodingName.HARMONY_GPT_OSS)
```

> Note: we load the model but **not** the `AutoTokenizer` for prompting — token IDs come from the Harmony encoding, so no chat template is ever involved. (You may still load the tokenizer if you want `decode()` for debugging.)

### 5.3 Render the Harmony prompt yourself → token IDs

```py
convo = Conversation.from_messages([
    Message.from_role_and_content(
        Role.SYSTEM,
        SystemContent.new(),                       # you set reasoning effort, dates, channels here
    ),
    Message.from_role_and_content(
        Role.DEVELOPER,
        DeveloperContent.new().with_instructions("You are a terse assistant."),
    ),
    Message.from_role_and_content(Role.USER, "What is 2 + 2?"),
])

# Ask the encoding to produce the exact token IDs, primed for the assistant to continue.
prefill_ids = enc.render_conversation_for_completion(convo, Role.ASSISTANT)

# The valid stop tokens for an assistant turn: <|return|> (done) and <|call|> (tool call).
stop_ids = enc.stop_tokens_for_assistant_actions()
```

### 5.4 Generate on raw token IDs

**[synthesized]** — combines the documented Harmony render/parse API with a standard Transformers `generate` call on explicit `input_ids`.

```py
input_ids = torch.tensor([prefill_ids], device=model.device)
attention_mask = torch.ones_like(input_ids)

out = model.generate(
    input_ids=input_ids,
    attention_mask=attention_mask,
    max_new_tokens=512,
    do_sample=True,
    temperature=1.0,
    eos_token_id=stop_ids,      # stop on <|return|> or <|call|>
)

# Keep only the newly generated tokens (strip the prompt you fed in).
completion_ids = out[0][input_ids.shape[-1]:].tolist()
```

Because you pass `input_ids` directly, the tokenizer never runs — no BOS injection, no template, nothing added to your sequence.

### 5.5 Parse the returned token IDs yourself

```py
messages = enc.parse_messages_from_completion_tokens(completion_ids, Role.ASSISTANT)

for m in messages:
    d = m.to_dict()
    print(d["channel"], "→", d["content"])
```

Route by channel (see §10): show `final`, keep `analysis` private, dispatch `commentary` tool calls.

### 5.6 Full end-to-end raw script (Transformers)

**[synthesized]**

```py
import torch
from transformers import AutoModelForCausalLM
from openai_harmony import (
    load_harmony_encoding, HarmonyEncodingName,
    Conversation, Message, Role, SystemContent, DeveloperContent,
)

MODEL = "openai/gpt-oss-20b"

model = AutoModelForCausalLM.from_pretrained(MODEL, torch_dtype="auto", device_map="auto")
enc = load_harmony_encoding(HarmonyEncodingName.HARMONY_GPT_OSS)

def run(user_text: str, reasoning: str = "medium", instructions: str = "You are helpful."):
    convo = Conversation.from_messages([
        Message.from_role_and_content(Role.SYSTEM, SystemContent.new()),  # see §9 to set reasoning effort
        Message.from_role_and_content(Role.DEVELOPER, DeveloperContent.new().with_instructions(instructions)),
        Message.from_role_and_content(Role.USER, user_text),
    ])
    prefill = enc.render_conversation_for_completion(convo, Role.ASSISTANT)
    stop = enc.stop_tokens_for_assistant_actions()

    ids = torch.tensor([prefill], device=model.device)
    out = model.generate(
        input_ids=ids,
        attention_mask=torch.ones_like(ids),
        max_new_tokens=1024,
        do_sample=True,
        temperature=1.0,
        eos_token_id=stop,
    )
    completion = out[0][ids.shape[-1]:].tolist()
    return enc.parse_messages_from_completion_tokens(completion, Role.ASSISTANT)

for m in run("Explain MXFP4 in one sentence."):
    d = m.to_dict()
    if d.get("channel") == "final":
        print("ANSWER:", d["content"])
    else:
        print(f"[{d.get('channel')}] (internal):", d["content"])
```

### 5.7 Fully by-hand (author the raw string, then tokenize)

If you want zero renderer library, author the Harmony **string** yourself (every token from §8) and tokenize it. Caveat: you must ensure the special tokens map to their exact IDs (e.g. `<|start|>` → 200006). Verify before trusting it.

```py
from transformers import AutoTokenizer
tok = AutoTokenizer.from_pretrained("openai/gpt-oss-20b")

harmony = (
    "<|start|>system<|message|>You are ChatGPT, a large language model trained by OpenAI.\n"
    "Knowledge cutoff: 2024-06\nCurrent date: 2025-06-28\n\nReasoning: high\n\n"
    "# Valid channels: analysis, commentary, final. Channel must be included for every message.<|end|>"
    "<|start|>user<|message|>What is 2 + 2?<|end|>"
    "<|start|>assistant"
)

# You authored the Harmony; the tokenizer only tokenizes the string you wrote.
input_ids = tok.encode(harmony)         # NOT apply_chat_template

# Sanity check the special tokens survived as single IDs before relying on this:
assert 200006 in input_ids  # <|start|>
```

> `tok.encode(your_string)` is **not** `apply_chat_template` — it adds no roles, no channels, no template. It only turns the exact characters you wrote into IDs. Prefer §5.3's renderer for correctness; use this only if you truly want no Harmony library. Decode outputs with `tok.decode(...)` and split channels by scanning for the special tokens in §8.

### 5.8 Multi-GPU (gpt-oss-120b)

```py
from transformers.distributed import DistributedConfig

device_map = {
    "distributed_config": DistributedConfig(enable_expert_parallel=1),
    "tp_plan": "auto",
}

model = AutoModelForCausalLM.from_pretrained(
    "openai/gpt-oss-120b",
    torch_dtype="auto",
    attn_implementation="kernels-community/vllm-flash-attn3",
    **device_map,
)
```

```bash
torchrun --nproc_per_node=4 generate.py
```

---

## 6. Raw inference with vLLM offline (direct token sampling)

Use vLLM's **offline `LLM` class** (not the server). You still render/parse Harmony yourself; vLLM just runs the forward passes on token IDs. This block is verbatim from the vLLM cookbook's "direct sampling" path.

### 6.1 Install

```shell
uv venv --python 3.12 --seed
source .venv/bin/activate
uv pip install --pre vllm==0.10.1+gptoss \
    --extra-index-url https://wheels.vllm.ai/gpt-oss/ \
    --extra-index-url https://download.pytorch.org/whl/nightly/cu128 \
    --index-strategy unsafe-best-match
uv pip install openai-harmony
```

### 6.2 Render → generate on `prompt_token_ids` → parse

```py
import json
from openai_harmony import (
    HarmonyEncodingName,
    load_harmony_encoding,
    Conversation,
    Message,
    Role,
    SystemContent,
    DeveloperContent,
)
from vllm import LLM, SamplingParams

encoding = load_harmony_encoding(HarmonyEncodingName.HARMONY_GPT_OSS)

convo = Conversation.from_messages(
    [
        Message.from_role_and_content(Role.SYSTEM, SystemContent.new()),
        Message.from_role_and_content(
            Role.DEVELOPER,
            DeveloperContent.new().with_instructions("Always respond in riddles"),
        ),
        Message.from_role_and_content(Role.USER, "What is the weather like in SF?"),
    ]
)

prefill_ids = encoding.render_conversation_for_completion(convo, Role.ASSISTANT)
stop_token_ids = encoding.stop_tokens_for_assistant_actions()

llm = LLM(
    model="openai/gpt-oss-120b",
    trust_remote_code=True,
)

sampling = SamplingParams(
    max_tokens=128,
    temperature=1,
    stop_token_ids=stop_token_ids,
)

outputs = llm.generate(
    prompts=[{"prompt_token_ids": prefill_ids}],
    sampling_params=sampling,
)

gen = outputs[0].outputs[0]
text = gen.text
output_tokens = gen.token_ids

entries = encoding.parse_messages_from_completion_tokens(output_tokens, Role.ASSISTANT)

for message in entries:
    print(f"{json.dumps(message.to_dict())}")
```

Everything here is under your control: `prefill_ids` is what you rendered, `output_tokens` are the raw IDs the model produced, and `parse_messages_from_completion_tokens` is you parsing them. No server, no OpenAI schema, no hidden Harmony.

---

## 7. Raw inference with the OpenAI reference implementation

OpenAI published two reference runtimes in the [`openai/gpt-oss`](https://github.com/openai/gpt-oss) repo — a **basic PyTorch** implementation and an **optimized Triton** implementation (the vLLM path was verified against them). These operate directly on tokens and are the most bare-metal way to run the weights.

- `python -m gpt_oss.generate …` — low-level generation over tokens.
- The repo also ships Harmony-aware chat entry points; for the "you own Harmony" goal, use the token-level generation path and render/parse with `openai-harmony` yourself.

> The cookbook references these implementations but does not enumerate every CLI flag. Clone the repo and read its README for exact invocation; treat this as the "no HF, no vLLM" escape hatch when you want to control the inference loop end to end.

---

## 8. The Harmony format (spec)

You must produce valid Harmony and parse it back. This is the complete surface.

### 8.1 Roles (precedence high → low)

`system` > `developer` > `user` > `assistant` > `tool`

| Role | Purpose |
|------|---------|
| **system** | Reasoning effort, metadata (knowledge cutoff, current date), valid channels, built-in tools |
| **developer** | The "system prompt" — instructions and available **function** tools |
| **user** | Input to the model |
| **assistant** | Model output (messages or tool calls), tagged with a channel |
| **tool** | Output returned from a tool call; the role name is the tool's name |

### 8.2 Channels (assistant output splits across these)

| Channel | Contents | Show to users? |
|---------|----------|----------------|
| **final** | The user-facing answer | ✅ Yes |
| **analysis** | Chain-of-thought reasoning | ❌ **No** — not safety-filtered |
| **commentary** | Function tool calls (and action-plan preambles) | ⚠️ Usually internal |

> *"Messages in the analysis channel do not adhere to the same safety standards as final messages do. Avoid showing these to end-users."*

### 8.3 Special tokens (`o200k_harmony` encoding)

| Token | Purpose | ID |
|-------|---------|-----|
| `<\|start\|>` | Begin a message (header info) | 200006 |
| `<\|end\|>` | End a message | 200007 |
| `<\|message\|>` | Header → content transition | 200008 |
| `<\|channel\|>` | Transition to channel info | 200005 |
| `<\|constrain\|>` | Marks data-type in a tool call | 200003 |
| `<\|return\|>` | Model done sampling — valid stop token | 200002 |
| `<\|call\|>` | Model wants to call a tool — valid stop token | 200012 |

### 8.4 Message structure

```
<|start|>{header}<|message|>{content}<|end|>
```

Header carries the role (and for tool calls, recipient + channel). Multiple messages are separated by `<|end|>`; the assistant ends a turn with `<|return|>` or requests a tool with `<|call|>`.

### 8.5 Minimal chat exchange

**Prompt you provide (note it ends primed for the assistant):**

```
<|start|>user<|message|>What is 2 + 2?<|end|>
<|start|>assistant
```

**What the model generates:**

```
<|channel|>analysis<|message|>User asks: "What is 2 + 2?" Simple arithmetic. Provide answer.<|end|>
<|start|>assistant<|channel|>final<|message|>2 + 2 = 4.<|return|>
```

### 8.6 System message format

Include: model identity, knowledge cutoff + current date, reasoning effort, valid channels, and (if functions exist) the note that function calls go to `commentary`.

Minimal:

```
<|start|>system<|message|>You are ChatGPT, a large language model trained by OpenAI.
Knowledge cutoff: 2024-06
Current date: 2025-06-28

Reasoning: high

# Valid channels: analysis, commentary, final. Channel must be included for every message.<|end|>
```

With function tools available:

```
<|start|>system<|message|>You are ChatGPT, a large language model trained by OpenAI.
Knowledge cutoff: 2024-06
Current date: 2025-06-28

Reasoning: high

# Valid channels: analysis, commentary, final. Channel must be included for every message.
Calls to these tools must go to the commentary channel: 'functions'.<|end|>
```

### 8.7 Developer message format

```
<|start|>developer<|message|># Instructions

{instructions}<|end|>
```

### 8.8 Reasoning levels

Set `Reasoning: low | medium | high` in the system message. Default is **medium**. CoT → `analysis`; answer → `final`.

---

## 9. Rendering Harmony yourself

Two ways to produce step [1]'s token IDs. Both are "you generating Harmony yourself."

### 9.1 With the `openai-harmony` renderer (recommended)

You author every message, role, channel, recipient, and content type explicitly:

```python
from openai_harmony import (
    Author,
    Conversation,
    DeveloperContent,
    HarmonyEncodingName,
    Message,
    Role,
    SystemContent,
    ToolDescription,
    load_harmony_encoding,
    ReasoningEffort
)

encoding = load_harmony_encoding(HarmonyEncodingName.HARMONY_GPT_OSS)

system_message = (
    SystemContent.new()
        .with_reasoning_effort(ReasoningEffort.HIGH)
        .with_conversation_start_date("2025-06-28")
)

developer_message = (
    DeveloperContent.new()
        .with_instructions("Always respond in riddles")
        .with_function_tools(
            [
                ToolDescription.new(
                    "get_current_weather",
                    "Gets the current weather in the provided location.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "location": {
                                "type": "string",
                                "description": "The city and state, e.g. San Francisco, CA",
                            },
                            "format": {
                                "type": "string",
                                "enum": ["celsius", "fahrenheit"],
                                "default": "celsius",
                            },
                        },
                        "required": ["location"],
                    },
                ),
            ]
	)
)

convo = Conversation.from_messages(
    [
        Message.from_role_and_content(Role.SYSTEM, system_message),
        Message.from_role_and_content(Role.DEVELOPER, developer_message),
        Message.from_role_and_content(Role.USER, "What is the weather in Tokyo?"),
        Message.from_role_and_content(
            Role.ASSISTANT,
            'User asks: "What is the weather in Tokyo?" We need to use get_current_weather tool.',
        ).with_channel("analysis"),
        Message.from_role_and_content(Role.ASSISTANT, '{"location": "Tokyo"}')
        .with_channel("commentary")
        .with_recipient("functions.get_current_weather")
        .with_content_type("<|constrain|> json"),
        Message.from_author_and_content(
            Author.new(Role.TOOL, "functions.get_current_weather"),
            '{ "temperature": 20, "sunny": true }',
        ).with_channel("commentary"),
    ]
)

tokens = encoding.render_conversation_for_completion(convo, Role.ASSISTANT)
# `tokens` is a list[int] of token IDs — feed straight to model.generate / LLM.generate.
```

`render_conversation_for_completion(convo, Role.ASSISTANT)` returns the token IDs primed for the assistant to continue. That list is what you pass to the model in §5/§6.

### 9.2 Setting reasoning effort and dates

```python
SystemContent.new() \
    .with_reasoning_effort(ReasoningEffort.LOW)     # or MEDIUM / HIGH
    .with_conversation_start_date("2025-06-28")
```

### 9.3 By-hand string authoring

Write the raw string using the tokens in §8, then tokenize (see §5.7). Maximum control, most error-prone — you own correctness of every `<|…|>` marker. Prefer §9.1 unless you specifically want no library.

---

## 10. Parsing the response yourself

The model returns a flat list of token IDs encoding **one or more messages across channels**. Parsing = step [3].

### 10.1 Batch parse (after generation completes)

```python
messages = encoding.parse_messages_from_completion_tokens(completion_ids, Role.ASSISTANT)
for m in messages:
    print(m.to_dict())   # includes role, channel, recipient, content
```

Then route:

```python
final_text   = [m for m in messages if m.to_dict().get("channel") == "final"]
analysis_cot = [m for m in messages if m.to_dict().get("channel") == "analysis"]   # keep private
tool_calls   = [m for m in messages if m.to_dict().get("channel") == "commentary"] # dispatch
```

### 10.2 Streaming parse (token by token)

Use `StreamableParser` to route each delta as it's produced (e.g. stream `final` to a UI while buffering `analysis` privately):

```python
from openai_harmony import (
    load_harmony_encoding,
    Role,
    StreamableParser,
    HarmonyEncodingName
)

encoding = load_harmony_encoding(HarmonyEncodingName.HARMONY_GPT_OSS)
stream = StreamableParser(encoding, role=Role.ASSISTANT)

tokens = [
    200005,35644,200008,1844,31064,25,392,4827,382,220,17,659,220,17,16842,12295,81645,
    13,51441,6052,13,200007,200006,173781,200005,17196,200008,17,659,220,17,314,220,19,
    13,200002
]

for token in tokens:
    stream.process(token)
    print("--------------------------------")
    print("current_role", stream.current_role)
    print("current_channel", stream.current_channel)
    print("last_content_delta", stream.last_content_delta)
    print("current_content_type", stream.current_content_type)
    print("current_recipient", stream.current_recipient)
    print("current_content", stream.current_content)
```

Gate output on `stream.current_channel`: only emit `last_content_delta` to the user when the channel is `final`.

### 10.3 Managing multi-turn history yourself

Since nothing manages conversation state for you:

- **Append the model's own messages** back into your `Conversation` before the next turn (re-render the whole thing to token IDs each turn).
- **Stop-token bookkeeping:** when storing the assistant reply, **replace the trailing `<|return|>` with `<|end|>`** so history contains fully-formed messages.
- **Drop or keep CoT** per the rules in §11.

---

## 11. Raw chain-of-thought (CoT) handling

The single most important operational rule.

### 11.1 What it is

The model emits a **raw chain-of-thought** on the `analysis` channel, meant for *analysis and safety research by model implementors*. It is **crucial for tool calling** — tool calls happen as part of the CoT.

### 11.2 Why you must not show it raw

Raw CoT:
- is **not** held to `final`-output safety standards (may contain harmful content), and
- **may leak** things you didn't intend to expose — e.g. rules from your developer/system instructions.

**Never render raw `analysis` content to end-users.** If you want to display reasoning, show a **summary** produced by a separate summarizer model that blocks harmful content (mirrors OpenAI's production behavior).

### 11.3 The drop rules (managing CoT across turns)

- CoT → `analysis`; final answer → `final`.
- **After a `final`-channel message, drop all previous `analysis` messages** on the next sampling turn: *"you should drop any previous CoT content on subsequent sampling if the responses by the assistant ended in a message to the final channel."*
- **Exception — tool calls:** when a turn ends in a tool call (not a `final` message), **pass the previous CoT back** with the tool output — it's the context the model needs to continue. `commentary` tool calls persist across turns.

```
last assistant message on `final`   → drop prior analysis before next turn.
last assistant action was a tool call → KEEP analysis + tool output for next turn.
```

### 11.4 If you expose an API over your raw runtime

Even self-hosted, if you wrap your raw loop in an API, return CoT in the right field (this is also what verification checks — §14):

**Chat Completions style** — raw CoT as a `reasoning` property on the message (and on streaming deltas); returned by default unless `reasoning: { exclude: true }`.

**Responses API style** — raw CoT inside a reasoning item:

```typescript
type ReasoningItem = {
  id: string;
  type: "reasoning";
  summary: SummaryContent[];
  content: ReasoningTextContent[];
};

type ReasoningTextContent = {
  type: "reasoning_text";
  text: string;
};
```

Streaming events: `response.reasoning_text.delta` and `response.reasoning_text.done`.

---

## 12. Tool calling — the manual loop

You dispatch tools yourself; the model just tells you what to call, on `commentary`.

### 12.1 Declaring tools (developer message)

Via the renderer, use `DeveloperContent.new().with_function_tools([...])` (§9.1). The underlying Harmony uses a TypeScript-like `functions` namespace:

```
namespace functions {

// Gets the location of the user.
type get_location = () => any;

// Gets the current weather in the provided location.
type get_current_weather = (_: {
// The city and state, e.g. San Francisco, CA
location: string,
format?: "celsius" | "fahrenheit", // default: celsius
}) => any;

} // namespace functions
```

Conventions: no-arg → `() => any`; args use param name `_` with inline types; descriptions in `//` comments; always return `any`; wrap in the `functions` namespace.

### 12.2 Recognizing a tool call in the output

The model emits on `commentary`, names a `recipient`, may mark payload type with `<|constrain|>`, and stops on `<|call|>`:

```
<|channel|>analysis<|message|>Need to use function get_current_weather.<|end|><|start|>assistant<|channel|>commentary to=functions.get_current_weather <|constrain|>json<|message|>{"location":"San Francisco"}<|call|>
```

After parsing, you'll have: recipient `functions.get_current_weather`, channel `commentary`, JSON args `{"location":"San Francisco"}`.

### 12.3 Returning the tool result (you author this message)

```
<|start|>{toolname} to=assistant<|channel|>commentary<|message|>{output}<|end|>
```

Concretely:

```
<|start|>functions.get_current_weather to=assistant<|channel|>commentary<|message|>{"sunny": true, "temperature": 20}<|end|>
```

With the renderer:

```python
from openai_harmony import Author, Message, Role
tool_msg = Message.from_author_and_content(
    Author.new(Role.TOOL, "functions.get_current_weather"),
    '{"sunny": true, "temperature": 20}',
).with_channel("commentary")
```

### 12.4 The loop

```
1. Render system + developer(tools) + user → token IDs → generate.
2. Parse output. If it ends in a `final` message → done, show final.
3. If it ends in a tool call (<|call|>):
     a. Execute the tool yourself.
     b. Append to the conversation: the model's analysis + tool-call message AND your tool-result message.
        (KEEP the analysis CoT here — the tool-call exception, §11.3.)
     c. Re-render the whole conversation → generate again.
4. Repeat until a `final` message. Then drop CoT for the next user turn.
```

---

## 13. Built-in tools, structured output, preambles

### 13.1 Built-in tools (declared in the system message)

- **Browser tool** — functions `search`, `open`, `find`; requests go to `analysis`. `cursor` shows in brackets before each browsing display; citations use `【{cursor}†L{line}】`.
- **Python tool** — stateful Jupyter env at `/mnt/data`, `python` recipient via `analysis`, output internal, **120s** timeout.

Built-in tools generally emit on `analysis`; **function** tools emit on `commentary`.

### 13.2 Preambles (action plans)

Before multiple tool calls the model may emit a plan on `commentary`:

```
<|channel|>analysis<|message|>{long chain of thought}<|end|><|start|>assistant<|channel|>commentary<|message|>**Action plan**:
1. [Step 1]
2. [Step 2]
---
Will start executing the plan step by step<|end|>
```

### 13.3 Structured output

Declare a response format in the developer message:

```
# Response Formats

## {format name}

// {description}
{schema}<|end|>
```

Example:

```
<|start|>developer<|message|># Instructions

You are a helpful shopping assistant

# Response Formats

## shopping_list

{"properties":{"items":{"type":"array","description":"entries on the shopping list","items":{"type":"string"}}},"type":"object"}<|end|>
```

> This **influences** behavior but does **not guarantee** schema adherence. Since you own sampling, enforce a **custom grammar / constrained decoding** for strict compliance (e.g. vLLM's guided decoding, or a logits processor in Transformers).

---

## 14. Verifying your implementation

Because you're doing Harmony rendering/parsing and MXFP4 inference yourself, verify it. Three things make gpt-oss different — and each is where raw implementations break:

1. **Harmony format** — required for correct function calling and generation quality.
2. **Chain-of-thought handling** — raw CoT must be returned/passed back with tool outputs, not just displayed.
3. **Inference architecture** — **MXFP4** weights need adapted inference code.

**Reference implementations:** basic **PyTorch** + optimized **Triton** (in `openai/gpt-oss`); **vLLM** was verified correct.

### 14.1 Compatibility test (API shapes + tool calling)

```shell
git clone https://github.com/openai/gpt-oss.git
cd gpt-oss/compatibility-test/
npm install
npm start -- --provider <your-provider-name>
```

**Pass criteria:** *0 invalid requests and over 90% on both `pass@k` and `pass^k`.*

Debug flags:

```shell
DEBUG=openai-agents:openai npm start -- --provider <provider-name>
npm start -- --provider <provider-name> -n 1
npm start -- --provider <provider-name> --streaming
```

### 14.2 Numerical correctness (eval benchmarks)

```bash
python -m gpt_oss.evals --base-url http://localhost:8000/v1 --eval aime25 \
  --sampler responses --model openai/gpt-oss-120b --reasoning-effort high
```

```bash
python -m gpt_oss.evals --base-url http://localhost:8000/v1 --eval aime25 \
  --sampler chat_completions --model openai/gpt-oss-120b --reasoning-effort high
```

Attempts per problem: **AIME = 16**, **GPQA = 8**, **Healthbench = 1**. Compare to published numbers (e.g. Artificial Analysis).

### 14.3 Common pitfalls (raw pipelines hit all three)

- **Incorrect Harmony mapping → cascading generation issues.** (Wrong headers/channels/stop tokens.)
- **Omitting raw CoT in tool-call turns → broken tool-call chains.** (You dropped analysis when you should have kept it.)
- **MXFP4 weight format → requires specialized inference code.** (Wrong dtype/kernels → garbage or OOM.)

---

## 15. Fine-tuning (still a raw model)

Fine-tuning produces new weights but the runtime contract is unchanged: it's still a raw Harmony model. Worked example: LoRA fine-tune of **gpt-oss-20b** for multilingual reasoning.

### 15.1 Install

```python
%pip install torch --index-url https://download.pytorch.org/whl/cu128
```

```python
%pip install "trl>=0.20.0" "peft>=0.17.0" "transformers>=4.55.0" trackio
```

```python
from huggingface_hub import notebook_login
notebook_login()
```

**Hardware:** single **H100 (80 GB)**. Smaller GPUs → reduce batch size and sequence length.

### 15.2 Dataset

```python
from datasets import load_dataset
dataset = load_dataset("HuggingFaceH4/Multilingual-Thinking", split="train")
```

1,000 examples of CoT reasoning in French/Spanish/German. In Harmony terms, the assistant turn carries `thinking` (→ `analysis`) and `content` (→ `final`).

### 15.3 Load base model (dequantize MXFP4 → bf16 for training)

```python
import torch
from transformers import AutoModelForCausalLM, Mxfp4Config

quantization_config = Mxfp4Config(dequantize=True)
model_kwargs = dict(
    attn_implementation="eager",
    torch_dtype=torch.bfloat16,
    quantization_config=quantization_config,
    use_cache=False,
    device_map="auto",
)

model = AutoModelForCausalLM.from_pretrained("openai/gpt-oss-20b", **model_kwargs)
```

### 15.4 LoRA config (MoE-aware — target expert projections)

```python
from peft import LoraConfig, get_peft_model

peft_config = LoraConfig(
    r=8,
    lora_alpha=16,
    target_modules="all-linear",
    target_parameters=[
        "7.mlp.experts.gate_up_proj",
        "7.mlp.experts.down_proj",
        "15.mlp.experts.gate_up_proj",
        "15.mlp.experts.down_proj",
        "23.mlp.experts.gate_up_proj",
        "23.mlp.experts.down_proj",
    ],
)
peft_model = get_peft_model(model, peft_config)
peft_model.print_trainable_parameters()
```

### 15.5 Train

```python
from trl import SFTConfig

training_args = SFTConfig(
    learning_rate=2e-4,
    gradient_checkpointing=True,
    num_train_epochs=1,
    logging_steps=1,
    per_device_train_batch_size=4,
    gradient_accumulation_steps=4,
    max_length=2048,
    warmup_ratio=0.03,
    lr_scheduler_type="cosine_with_min_lr",
    lr_scheduler_kwargs={"min_lr_rate": 0.1},
    output_dir="gpt-oss-20b-multilingual-reasoner",
    report_to="trackio",
    push_to_hub=True,
)
```

Effective batch size = 4 × 4 = **16**.

```python
from trl import SFTTrainer

trainer = SFTTrainer(
    model=peft_model,
    args=training_args,
    train_dataset=dataset,
    processing_class=tokenizer,   # tokenizer = AutoTokenizer.from_pretrained("openai/gpt-oss-20b")
)
trainer.train()
```

~18 minutes on an H100.

### 15.6 Save / push

```python
trainer.save_model(training_args.output_dir)
trainer.push_to_hub(dataset_name="HuggingFaceH4/Multilingual-Thinking")
```

Restart the kernel afterward to free memory.

### 15.7 Raw inference on the fine-tuned adapter

Load base + adapter, merge, then drive it with **your own Harmony** (as in §5 — do not fall back to `apply_chat_template` if you want to stay raw):

```python
from transformers import AutoModelForCausalLM
from peft import PeftModel

model_kwargs = dict(attn_implementation="eager", torch_dtype="auto", use_cache=True, device_map="auto")
base_model = AutoModelForCausalLM.from_pretrained("openai/gpt-oss-20b", **model_kwargs).cuda()

peft_model_id = "gpt-oss-20b-multilingual-reasoner"
model = PeftModel.from_pretrained(base_model, peft_model_id)
model = model.merge_and_unload()

# Then: render Harmony yourself (§9) → model.generate(input_ids=…) (§5.4) → parse (§10).
```

### 15.8 Reproducibility note (your stack)

The cookbook uses `report_to="trackio"`. For your runs:

- **Seed everything** — Python `random`, `numpy`, `torch` (+ CUDA deterministic flags).
- **Tracking:** W&B primary, MLflow secondary, CSV fallback (config-switchable); `SFTConfig(report_to=...)` accepts `"wandb"`/`"mlflow"`.
- **Snapshot** run config + git SHA + dataset hash per run; pin `transformers`/`trl`/`peft`/`torch`.

---

## 16. End-to-end raw recipes

### 16.1 One-shot answer, fully raw (Transformers)

```py
# See §5.6 for the complete script.
# render (§9) → model.generate(input_ids=…) (§5.4) → parse (§10) → show only `final`.
```

### 16.2 One-shot answer, fully raw (vLLM offline)

```py
# See §6.2 verbatim: render → LLM.generate(prompt_token_ids=…) → parse.
```

### 16.3 Tool-calling loop, fully raw

```
render(system+developer(tools)+user) → generate
  └─ parse output
       ├─ ends in `final`  → show final, drop CoT, wait for next user turn
       └─ ends in `<|call|>` (commentary tool call)
             → run the tool yourself
             → append [assistant analysis + tool-call msg] + [your tool-result msg]   # keep CoT!
             → re-render whole conversation → generate → repeat
```

### 16.4 Streaming a clean answer while hiding reasoning

```py
# StreamableParser (§10.2): forward last_content_delta to the UI
# ONLY when stream.current_channel == "final"; buffer "analysis" privately.
```

---

## 17. Gotchas & quick reference

**The raw pipeline, in one line:** `you render Harmony → token IDs → model.generate(ids) → token IDs → you parse Harmony`.

**Must-dos**
- ✅ Author Harmony yourself (renderer in §9.1, or by-hand in §9.3). Never `apply_chat_template`.
- ✅ Feed the model **token IDs** (`input_ids=` in Transformers, `prompt_token_ids` in vLLM offline). No server.
- ✅ Stop on `<|return|>` **and** `<|call|>` (use `enc.stop_tokens_for_assistant_actions()`).
- ✅ Show only `final`; keep `analysis` private.
- ✅ Keep CoT across turns **only** when the turn ends in a tool call; otherwise drop it after a `final` message.
- ✅ When storing history, swap trailing `<|return|>` → `<|end|>`.
- ✅ On older GPUs, dequantize MXFP4 to bf16 (expect ~48 GB for 20b).

**Don't**
- ❌ Don't use `apply_chat_template`, `transformers serve`, `vllm serve` + `chat.completions`/`responses`, Ollama, or llama.cpp — all auto-convert to Harmony.
- ❌ Don't show raw `analysis` CoT to users (unsafe + may leak your instructions).
- ❌ Don't drop CoT mid tool-call chain — it breaks tool calling.
- ❌ Don't expect schema adherence from a Response Format block alone — use constrained decoding.

**Special tokens (stops):** `<|return|>` = 200002 (done); `<|call|>` = 200012 (tool call). Both valid stops.
**Channels:** `final` (show) · `analysis` (hide) · `commentary` (tool calls / preambles).
**Roles:** `system > developer > user > assistant > tool`.
**Reasoning:** `low | medium | high` (default `medium`), set in the system message.
**Models:** `openai/gpt-oss-20b` (~16 GB MXFP4) · `openai/gpt-oss-120b` (≥60 GB, single H100 or multi-GPU).
**Raw runtimes:** Transformers `model.generate(input_ids=…)` (§5) · vLLM offline `LLM.generate(prompt_token_ids=…)` (§6) · OpenAI reference PyTorch/Triton (§7).

---

### Source links

- Harmony: <https://developers.openai.com/cookbook/articles/openai-harmony>
- Verifying implementations: <https://developers.openai.com/cookbook/articles/gpt-oss/verifying-implementations>
- Handle raw CoT: <https://developers.openai.com/cookbook/articles/gpt-oss/handle-raw-cot>
- Fine-tune with Transformers: <https://developers.openai.com/cookbook/articles/gpt-oss/fine-tune-transfomers>
- gpt-oss topic index: <https://developers.openai.com/cookbook/topic/gpt-oss>
- Reference implementation & tests: <https://github.com/openai/gpt-oss>
