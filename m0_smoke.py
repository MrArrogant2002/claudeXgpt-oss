"""M0 smoke test — prove the raw Harmony round-trip end to end.

  render a Harmony prompt  ->  llama.cpp /completion (token IDs)  ->  parse channels

Run this FIRST on the machine that has the model + llama.cpp server running.
It validates the one risky assumption the whole agent depends on: that the raw
/completion endpoint returns output token IDs we can parse.

Usage:
    python m0_smoke.py
    AGENT_BASE_URL=http://localhost:8081 python m0_smoke.py     # override server

Expected: an 'analysis' channel (reasoning) and a 'final' channel saying 4.
"""

import sys

from agent import config, inference
from agent import harmony_codec as hc


def main():
    # 0. preflight: is the server up?
    try:
        h = inference.health()
        print(f"[ok] server up at {config.BASE_URL}  ({h})")
    except Exception as e:
        print(f"[error] cannot reach llama.cpp at {config.BASE_URL}: {e}")
        print("Start it on the model machine, e.g.:")
        print("  llama-server -m gpt-oss-20b.gguf -c 8192 --port 8081 -ngl 999")
        print("(No --jinja needed: we render Harmony ourselves and use /completion.)")
        sys.exit(1)

    # 1. render our own Harmony prompt -> token IDs
    prefill, stop = hc.render(
        [hc.user_message("What is 2 + 2? Answer in one short sentence.")],
        reasoning="low",
        instructions="You are a helpful assistant.",
    )
    print(f"[render] {len(prefill)} prompt tokens; stop tokens = {stop}")

    # 2. raw completion -> output token IDs
    try:
        tokens, raw = inference.complete(prefill, stop_ids=stop, max_tokens=512)
    except inference.InferenceError as e:
        print(f"[error] {e}")
        sys.exit(2)
    stopped = raw.get("stopped_eos", raw.get("stop", raw.get("stop_type")))
    print(f"[infer] {len(tokens)} output tokens (stopped={stopped})")

    # 3. parse output token IDs -> channel messages
    msgs = hc.parse(tokens)
    final_text = ""
    for m in msgs:
        f = hc.msg_fields(m)
        label = f["channel"] or "?"
        if f["recipient"]:
            label += f" -> {f['recipient']}"
        print(f"\n--- {label} ---")
        print(f["content"])
        if f["channel"] == "final":
            final_text += f["content"]

    print("\n" + "=" * 60)
    if final_text.strip():
        print("[PASS] round-trip works — got a final answer.")
    else:
        print(
            "[WARN] no 'final' channel content. Try a bigger --max-tokens or "
            "reasoning='low'; confirm the model is gpt-oss."
        )


if __name__ == "__main__":
    main()
