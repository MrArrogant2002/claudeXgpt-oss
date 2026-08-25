"""
Smoke test for gpt-oss-20b served by llama.cpp.

Uses llama.cpp's OpenAI-compatible /v1/chat/completions endpoint, which applies
gpt-oss's Harmony template automatically (requires launching with --jinja).
Building Harmony ourselves + the raw /completion endpoint is Phase 2 — see
build-plan.md. This file is just a "is the model alive and answering?" check.

Why the original (Qwen) script returned nothing on gpt-oss:
  * gpt-oss emits a big 'analysis' (chain-of-thought) channel BEFORE the answer.
    llama.cpp puts that reasoning in `message.reasoning_content` and the real
    answer in `message.content`. With max_tokens=500 the reasoning consumed the
    whole budget, so `content` came back empty and it looked broken.
  * The script also only printed `content`, never the reasoning field.

Fixes here:
  * much larger max_tokens (reasoning + answer both need room)
  * configurable reasoning_effort (low = fastest smoke test)
  * prints reasoning AND answer separately (streaming)
  * preflight check that the server is up + which model it serves
  * clear diagnostics when the answer is empty

Launch the server first (note: port 8080 on this machine is taken by Apache/httpd,
which is why 8081 is used here):

    llama-server -m <path>/gpt-oss-20b.gguf --jinja -c 8192 --port 8081 -ngl 999

  --jinja   applies gpt-oss's embedded Harmony chat template (REQUIRED)
  -ngl 999  offload all layers to GPU (drop/lower if low on VRAM)
  -c 8192   context size
"""

import sys
from openai import OpenAI

# ---- config ----------------------------------------------------------------
BASE_URL = "http://localhost:8081/v1"  # match the --port you launch llama-server with
MODEL = "gpt-oss-20b"  # ignored by single-model llama.cpp servers
REASONING_EFFORT = "low"  # "low" | "medium" | "high"  (low = quick test)
MAX_TOKENS = 2048  # gpt-oss needs headroom for CoT + answer
TEMPERATURE = 0.7
STREAM = True
TIMEOUT = 600  # seconds

client = OpenAI(base_url=BASE_URL, api_key="test-key", timeout=TIMEOUT)


def get_reasoning(obj):
    """reasoning_content is a llama.cpp extension, not part of the OpenAI schema."""
    r = getattr(obj, "reasoning_content", None)
    if r is None:
        extra = getattr(obj, "model_extra", None)
        if extra:
            r = extra.get("reasoning_content")
    return r


# ---- preflight: is the server up? which model does it serve? ---------------
try:
    served = ", ".join(m.id for m in client.models.list().data) or "(none reported)"
    print(f"[ok] server reachable at {BASE_URL}")
    print(f"[ok] served model(s): {served}\n")
except Exception as e:
    print(f"[error] cannot reach llama.cpp at {BASE_URL}\n        {e}\n")
    print("Start the server (see the launch command at the top of this file), e.g.:")
    print("  llama-server -m gpt-oss-20b.gguf --jinja -c 8192 --port 8081 -ngl 999")
    print("and make sure BASE_URL above matches the --port you used.")
    sys.exit(1)

messages = [
    {"role": "system", "content": "You are a helpful programming assistant."},
    {
        "role": "user",
        "content": "Write a Python function to solve the 8 Queens problem.",
    },
]

print("Thinking...\n")

reasoning_parts, answer_parts = [], []
finish_reason = None

try:
    if STREAM:
        stream = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            stream=True,
            extra_body={"reasoning_effort": REASONING_EFFORT},
        )
        printed_reasoning_hdr = printed_answer_hdr = False
        for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason

            r = get_reasoning(choice.delta)
            if r:
                if not printed_reasoning_hdr:
                    print(
                        "----- reasoning (analysis channel — do NOT show end users) -----"
                    )
                    printed_reasoning_hdr = True
                print(r, end="", flush=True)
                reasoning_parts.append(r)

            c = choice.delta.content
            if c:
                if not printed_answer_hdr:
                    print("\n\n----- answer (final channel) -----")
                    printed_answer_hdr = True
                print(c, end="", flush=True)
                answer_parts.append(c)
        print()
    else:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            extra_body={"reasoning_effort": REASONING_EFFORT},
        )
        choice = resp.choices[0]
        finish_reason = choice.finish_reason
        r = get_reasoning(choice.message)
        if r:
            print("----- reasoning (analysis channel — do NOT show end users) -----")
            print(r)
            reasoning_parts.append(r)
        if choice.message.content:
            print("\n----- answer (final channel) -----")
            print(choice.message.content)
            answer_parts.append(choice.message.content)
except Exception as e:
    print(f"\n[error] request failed: {e}")
    print("If this is a 400/template error, launch llama-server with --jinja.")
    sys.exit(1)

# ---- diagnostics -----------------------------------------------------------
answer = "".join(answer_parts).strip()
print("\n" + "=" * 60)
print(f"finish_reason : {finish_reason}")
print(f"reasoning     : {len(''.join(reasoning_parts))} chars")
print(f"answer        : {len(answer)} chars")
if answer:
    print("[ok] got a final answer.")
else:
    print("[warn] the final answer (content) was EMPTY.")
    if finish_reason == "length":
        print("       cause: hit max_tokens while still reasoning.")
        print("       fix  : raise MAX_TOKENS, or set REASONING_EFFORT = 'low'.")
    else:
        print("       the model produced only reasoning. Try a larger MAX_TOKENS,")
        print(
            "       set REASONING_EFFORT = 'low', or confirm llama-server has --jinja."
        )
