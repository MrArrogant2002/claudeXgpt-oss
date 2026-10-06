#!/usr/bin/env python3
"""CSCD feasibility spike (build plan §2.4) — run this on the GPU box first.

Channel-scoped constrained decoding rests on assumptions about the local
`llama-server` build that cannot be checked on a machine without one. This script
checks them, end to end, against the real server, and writes a report that
doubles as the paper's reproducibility anchor.

    python scripts/spike_cscd.py --base-url http://localhost:8081

Checks, in order of how badly a failure hurts:

  1. raw /completion accepts a token-ID prompt and returns token IDs
  2. `grammar` is honoured alongside a token-ID prompt
  3. `json_schema` is honoured alongside a token-ID prompt
  4. `cache_prompt` reuses the KV cache across a phase hand-off
     (if not, CSCD costs a full re-prefill per phase and the latency story changes)
  5. `seed` makes a completion reproducible
  6. a full CSCD-I decode produces a canonical tool call

Exit status is 0 only when every check that CSCD-I requires has passed. Check 2
failing is survivable (CSCD-I needs only `json_schema`); checks 1, 3 and 6 are not.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("TIKTOKEN_RS_CACHE_DIR", str(REPO / "vendor" / "tiktoken"))

import requests  # noqa: E402


def strip_specials(enc, ids):
    """Drop special tokens from a constrained generation.

    llama.cpp appends an end-of-generation token once a grammar or JSON schema is
    satisfied, so a perfectly conformant response still arrives as
    `grep<|endoftext|>` or `{...}<|call|>`. Comparing or json.loads-ing the raw
    decode reports a valid result as a failure.
    """
    out = []
    for t in ids or []:
        try:
            if enc.is_special_token(t):
                continue
        except Exception:
            pass
        out.append(t)
    return out


RESET, BOLD ="\033[0m", "\033[1m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"


class Spike:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.url = base_url.rstrip("/") + "/completion"
        self.props_url = base_url.rstrip("/") + "/props"
        self.timeout = timeout
        self.results: list[dict[str, Any]] = []

    # --- plumbing ----------------------------------------------------------
    def post(self, body: dict[str, Any]) -> dict[str, Any]:
        r = requests.post(self.url, json=body, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def record(self, name: str, ok: bool, required: bool, detail: str, **extra: Any) -> bool:
        self.results.append(
            {"check": name, "ok": ok, "required": required, "detail": detail, **extra}
        )
        mark = f"{GREEN}PASS{RESET}" if ok else (
            f"{RED}FAIL{RESET}" if required else f"{YELLOW}WARN{RESET}"
        )
        print(f"  [{mark}] {name}: {detail}")
        return ok

    # --- checks ------------------------------------------------------------
    def check_server(self) -> dict[str, Any]:
        print(f"{BOLD}0. server{RESET}")
        try:
            props = requests.get(self.props_url, timeout=self.timeout).json()
        except Exception as e:
            self.record("server reachable", False, True, f"{type(e).__name__}: {e}")
            return {}
        gen = props.get("default_generation_settings") or {}
        n_ctx = gen.get("n_ctx") or props.get("n_ctx")
        self.record("server reachable", True, True, f"n_ctx={n_ctx}")
        return props

    def check_token_prompt(self, enc) -> bool:
        print(f"{BOLD}1. token-ID prompt + return_tokens{RESET}")
        ids = enc.encode("2 + 2 =", allowed_special="all")
        try:
            data = self.post(
                {"prompt": ids, "n_predict": 8, "return_tokens": True, "temperature": 0}
            )
        except Exception as e:
            return self.record("token-ID prompt", False, True, f"{type(e).__name__}: {e}")
        toks = data.get("tokens")
        if not toks:
            return self.record(
                "token-ID prompt", False, True,
                "no 'tokens' in response — build lacks return_tokens",
                keys=sorted(data),
            )
        return self.record(
            "token-ID prompt", True, True,
            f"{len(toks)} tokens back, decoded {enc.decode(toks)!r}",
        )

    def check_grammar(self, enc) -> bool:
        print(f"{BOLD}2. grammar + token-ID prompt (CSCD-G; optional for CSCD-I){RESET}")
        ids = enc.encode("The tool to call is ", allowed_special="all")
        grammar = 'root ::= "read" | "grep" | "glob"\n'
        try:
            data = self.post({
                "prompt": ids, "n_predict": 16, "return_tokens": True,
                "temperature": 0, "grammar": grammar,
            })
        except Exception as e:
            return self.record("grammar honoured", False, False, f"{type(e).__name__}: {e}")
        out = enc.decode(strip_specials(enc, data.get("tokens"))).strip()
        ok = out in ("read", "grep", "glob")
        return self.record(
            "grammar honoured", ok, False,
            f"produced {out!r}" + ("" if ok else " — NOT one of the alternatives"),
        )

    def check_json_schema(self, enc) -> bool:
        print(f"{BOLD}3. json_schema + token-ID prompt (CSCD-I REQUIRES this){RESET}")
        ids = enc.encode(
            "Call the read tool on agent/loop.py. Arguments: ", allowed_special="all"
        )
        schema = {
            "type": "object",
            "properties": {"path": {"type": "string"},
                           "start_line": {"type": "integer"}},
            "required": ["path"],
            "additionalProperties": False,
        }
        try:
            data = self.post({
                "prompt": ids, "n_predict": 64, "return_tokens": True,
                "temperature": 0, "json_schema": schema,
            })
        except Exception as e:
            return self.record("json_schema honoured", False, True,
                               f"{type(e).__name__}: {e}")
        out = enc.decode(strip_specials(enc, data.get("tokens"))).strip()
        try:
            obj = json.loads(out)
            ok = isinstance(obj, dict) and "path" in obj and set(obj) <= set(schema["properties"])
        except json.JSONDecodeError:
            ok = False
        return self.record("json_schema honoured", ok, True, f"produced {out!r}")

    def check_cache_prompt(self, enc) -> bool:
        print(f"{BOLD}4. cache_prompt across a phase hand-off{RESET}")
        # A realistic prefix. A 30-token probe says nothing useful: llama.cpp has
        # a minimum reuse threshold, and CSCD's hand-off happens after a full
        # agent prompt (tools + history), which is thousands of tokens.
        filler = ("A coding agent navigates a repository with tools rather than "
                  "an index. It lists directories, globs for files, greps for "
                  "symbols, and reads the lines that matter. ") * 40
        base = enc.encode(filler + "Summarise that. ", allowed_special="all")
        try:
            first = self.post({"prompt": base, "n_predict": 24, "return_tokens": True,
                               "temperature": 0, "cache_prompt": True})
            extended = base + (first.get("tokens") or [])
            second = self.post({"prompt": extended, "n_predict": 8, "return_tokens": True,
                                "temperature": 0, "cache_prompt": True})
        except Exception as e:
            return self.record("cache_prompt reuse", False, False,
                               f"{type(e).__name__}: {e}")
        evaluated = second.get("tokens_evaluated")
        if not isinstance(evaluated, int):
            evaluated = (second.get("timings") or {}).get("prompt_n")

        # The reported counter is not trustworthy on its own: some builds report
        # the whole prompt as evaluated even when the KV cache was reused. Time
        # it instead. A cold call on a fresh prefix versus a warm call on the
        # same one is the measurement that actually decides CSCD's overhead.
        cold_prefix = enc.encode(
            filler + "Now describe a different thing entirely. ",
            allowed_special="all",
        )
        try:
            t0 = time.monotonic()
            self.post({"prompt": cold_prefix, "n_predict": 1, "return_tokens": True,
                       "temperature": 0, "cache_prompt": True})
            cold_ms = (time.monotonic() - t0) * 1000
            t0 = time.monotonic()
            self.post({"prompt": cold_prefix, "n_predict": 1, "return_tokens": True,
                       "temperature": 0, "cache_prompt": True})
            warm_ms = (time.monotonic() - t0) * 1000
        except Exception as e:
            return self.record("cache_prompt reuse", False, False,
                               f"timing probe failed: {type(e).__name__}: {e}")

        ok = warm_ms < cold_ms * 0.6
        counter = (
            f"{evaluated} of {len(extended)} reported evaluated"
            if isinstance(evaluated, int) else "counter unavailable"
        )
        detail = (
            f"cold {cold_ms:.0f} ms vs warm {warm_ms:.0f} ms on a "
            f"{len(cold_prefix)}-token prefix ({counter})"
        )
        if not ok:
            detail += " — no measurable reuse; CSCD pays a re-prefill per phase"
        return self.record(
            "cache_prompt reuse", ok, False, detail,
            cold_ms=round(cold_ms, 1), warm_ms=round(warm_ms, 1),
            reported_evaluated=evaluated, prefix=len(cold_prefix),
        )

    def check_seed(self, enc) -> bool:
        print(f"{BOLD}5. seed reproducibility{RESET}")
        ids = enc.encode("Write one sentence about caching.", allowed_special="all")
        def twice(temperature: float, **extra: Any) -> bool:
            body = {"prompt": ids, "n_predict": 32, "return_tokens": True,
                    "temperature": temperature, "seed": 12345, **extra}
            a = self.post(dict(body))
            b = self.post(dict(body))
            return (a.get("tokens") or []) == (b.get("tokens") or [])

        try:
            sampled = twice(0.8)
            greedy = twice(0.0, top_k=1)
        except Exception as e:
            return self.record("seed reproducible", False, False,
                               f"{type(e).__name__}: {e}")

        # Greedy determinism is the one that matters: if even temperature 0 with
        # top_k 1 varies, the cause is numerical (batch composition), not
        # sampling, and no seed can fix it. Run the server with --parallel 1 and
        # report reliability@k regardless.
        detail = f"seeded sampling {'stable' if sampled else 'VARIES'}, "                  f"greedy {'stable' if greedy else 'VARIES'}"
        if not greedy:
            detail += " — numerical nondeterminism; use --parallel 1"
        elif not sampled:
            detail += " — seed is not honoured for sampled decoding"
        return self.record("seed reproducible", sampled and greedy, False, detail,
                           sampled_stable=sampled, greedy_stable=greedy)

    def check_end_to_end(self, enc) -> bool:
        print(f"{BOLD}6. end-to-end CSCD-I decode{RESET}")
        from types import SimpleNamespace

        from agent.decoding.client import LlamaCppClient
        from agent.decoding.strategies import build
        from agent.settings import SamplingSettings
        from agent import harmony_codec as hc

        tools = [
            SimpleNamespace(name="read", description="read a file",
                            parameters={"type": "object",
                                        "properties": {"path": {"type": "string"}},
                                        "required": ["path"]}),
            SimpleNamespace(name="grep", description="search",
                            parameters={"type": "object",
                                        "properties": {"pattern": {"type": "string"}},
                                        "required": ["pattern"]}),
        ]
        harmony_tools = [
            hc.tool_description(t.name, t.description, t.parameters) for t in tools
        ]
        prefill, _ = hc.render(
            [hc.user_message("Read the file agent/loop.py and tell me what it does.")],
            tools=harmony_tools,
            reasoning="low",
            instructions="You are a coding agent. Use the tools.",
        )
        client = LlamaCppClient(SamplingSettings(temperature=0.0, top_k=1, seed=7))
        try:
            t0 = time.monotonic()
            result = build("cscd_i", enc).generate(
                prefill, tools=tools, client=client, max_tokens=512
            )
            elapsed = (time.monotonic() - t0) * 1000
        except Exception as e:
            return self.record("CSCD-I end to end", False, True,
                               f"{type(e).__name__}: {e}")
        text = enc.decode(result.tokens)
        recipient = result.raw.get("recipient")
        ok = (
            recipient is not None
            and not result.raw.get("cscd_fallback")
            and text.count("to=") == 1
            and f"to=functions.{recipient}" in text
        )
        return self.record(
            "CSCD-I end to end", ok, True,
            f"{result.round_trips} round trips, {elapsed:.0f} ms, "
            f"recipient={result.raw.get('recipient')!r}",
            phases=[p.phase for p in result.phases],
            completion=text[:400],
        )

    # --- driver ------------------------------------------------------------
    def run(self) -> int:
        from openai_harmony import HarmonyEncodingName, load_harmony_encoding

        enc = load_harmony_encoding(HarmonyEncodingName.HARMONY_GPT_OSS)
        props = self.check_server()
        if not any(r["check"] == "server reachable" and r["ok"] for r in self.results):
            print(f"\n{RED}server unreachable; nothing else can be checked{RESET}")
            return 2

        self.check_token_prompt(enc)
        self.check_grammar(enc)
        self.check_json_schema(enc)
        self.check_cache_prompt(enc)
        self.check_seed(enc)
        self.check_end_to_end(enc)

        required_failed = [r for r in self.results if r["required"] and not r["ok"]]
        print()
        if required_failed:
            print(f"{RED}{BOLD}CSCD-I IS NOT FEASIBLE on this build.{RESET}")
            for r in required_failed:
                print(f"  - {r['check']}: {r['detail']}")
            print("  Update llama.cpp, or fall back to the repair-only arms and say so "
                  "in the paper.")
        else:
            print(f"{GREEN}{BOLD}CSCD-I is feasible on this build.{RESET}")
        self.write_report(props)
        return 1 if required_failed else 0

    def write_report(self, props: dict[str, Any]) -> None:
        out_dir = REPO / "docs" / "spikes"
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "base_url": self.url,
            "server_props": {
                k: props.get(k) for k in ("model_path", "n_ctx", "build_info", "chat_template")
            },
            "results": self.results,
        }
        (out_dir / "cscd-feasibility.json").write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )

        lines = [
            "# CSCD feasibility spike",
            "",
            f"Generated {payload['generated_at']} against `{self.url}`.",
            "",
            "Build plan §2.4. This file is the reproducibility anchor for the",
            "constrained-decoding contribution: it records what the server actually",
            "supports, not what the method assumes.",
            "",
            "| Check | Required | Result | Detail |",
            "|---|---|---|---|",
        ]
        for r in self.results:
            lines.append(
                f"| {r['check']} | {'yes' if r['required'] else 'no'} | "
                f"{'PASS' if r['ok'] else 'FAIL'} | {r['detail']} |"
            )
        lines += ["", "## Server", "", "```json",
                  json.dumps(payload["server_props"], indent=2, default=str), "```"]
        (out_dir / "cscd-feasibility.md").write_text("\n".join(lines), encoding="utf-8")
        print(f"report written to docs/spikes/cscd-feasibility.{{md,json}}")


def main() -> int:
    ap = argparse.ArgumentParser(description="CSCD feasibility spike")
    ap.add_argument("--base-url", default=os.environ.get("AGENT_BASE_URL",
                                                         "http://localhost:8081"))
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()
    print(f"{BOLD}CSCD feasibility spike{RESET}  ->  {args.base_url}\n")
    return Spike(args.base_url, args.timeout).run()


if __name__ == "__main__":
    raise SystemExit(main())
