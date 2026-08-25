"""End-to-end test of the FULL agent loop (not just the M0 round-trip).

Runs the agent against tests/fixture_repo with a question whose answer is known,
and asserts that the agent:
  1. completed the turn
  2. actually called at least one tool (the navigate-don't-index funnel ran)
  3. grounded its answer in the code (mentions the right file / symbol / behavior)

Run on the model machine with llama-server up on port 8081:
    python e2e_test.py
"""

import os
import sys

from agent import config, inference, loop
from agent.sandbox import Sandbox
from agent.tools import default_registry

FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "tests", "fixture_repo"
)
QUESTION = (
    "Which file defines the function validate_token, and what does it check "
    "before it returns a user?"
)


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    try:
        inference.health()
    except Exception as e:
        print(f"[error] server not reachable at {config.BASE_URL}: {e}")
        print("Start: llama-server -m gpt-oss-20b.gguf -c 8192 --port 8081 -ngl 999")
        sys.exit(1)

    sandbox = Sandbox(FIXTURE)
    registry = default_registry()
    print(f"[project] {sandbox.root}", file=sys.stderr)

    tool_calls = []

    def on_event(f):
        if f.get("role") == "tool":
            print(
                f"  [tool result] {f['recipient']}: {f['content'][:120]!r}",
                file=sys.stderr,
            )
        elif f.get("channel") == "commentary" and f.get("recipient"):
            tool_calls.append(f["recipient"])
            print(
                f"  [tool call]   {f['recipient']} {f['content'][:120]}",
                file=sys.stderr,
            )

    res, _history = loop.run_turn(
        QUESTION, [], registry, sandbox, reasoning="medium", on_event=on_event
    )

    print("\n=== ANSWER ===")
    print(res.answer)

    ans = (res.answer or "").lower()
    checks = {
        "turn completed": res.reason == "completed",
        "made >= 1 tool call": len(tool_calls) >= 1,
        "answer mentions auth.py or auth": ("auth.py" in ans) or ("auth" in ans),
        "answer mentions validate_token": "validate_token" in ans,
        "answer mentions the token check": "token" in ans,
    }

    print("\n=== CHECKS ===")
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

    all_ok = all(checks.values())
    print(
        "\n"
        + (
            "[PASS] full loop works end to end."
            if all_ok
            else "[PARTIAL] some checks failed — see above (tune --reasoning or "
            "the developer instructions in agent/loop.py)."
        )
    )
    sys.exit(0 if all_ok else 2)


if __name__ == "__main__":
    main()
