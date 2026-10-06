#!/usr/bin/env python3
"""Turn run traces into the paper's numbers.

    python scripts/analyze_trace.py runs/*.jsonl
    python scripts/analyze_trace.py runs/ --json reports/rq1.json

Groups every trace by decoding arm and reports, per arm:

  RQ1  structural tool-call failures -- the base rate the paper opens on
  RQ2  the same under each arm, plus round trips and latency (the cost)
  RQ3  dispatch surface: where each invocation was derived from

The per-call rate and the per-task rate are both reported, because they answer
different questions. A 99% per-call validity sounds excellent and still fails
most multi-step tasks: with n calls the chance of a clean task is p^n.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.trace import read_trace  # noqa: E402


def collect(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(p.rglob("*.jsonl")))
        elif p.is_file():
            files.append(p)
    return files


def summarise_run(records: list[dict[str, Any]]) -> dict[str, Any]:
    manifest = next((r for r in records if r.get("kind") == "manifest"), {})
    summary = next((r for r in records if r.get("kind") == "summary"), {})
    settings = manifest.get("settings") or {}
    counters = summary.get("counters") or {}

    events = [r for r in records if r.get("kind") == "event"]
    calls = [e for e in events if e.get("event") == "tool_call"]
    decodes = [e for e in events if e.get("event") == "decode"]
    phases = [e for e in events if e.get("event") == "decode_phase"]
    parses = [e for e in events if e.get("event") == "parse"]

    sources: dict[str, int] = defaultdict(int)
    for c in calls:
        sources[c.get("source", "header")] += 1

    structural_failures = (
        counters.get("strict_parse_failures", 0)
        + counters.get("schema_violations", 0)
        + counters.get("invalid_json_arguments", 0)
    )
    return {
        "arm": settings.get("decoding", "unconstrained"),
        "seed": (settings.get("sampling") or {}).get("seed"),
        "greedy": (settings.get("sampling") or {}).get("temperature") == 0.0,
        "turns": counters.get("turns", 0),
        "tool_calls": counters.get("tool_calls", 0),
        "structural_failures": structural_failures,
        "salvages": counters.get("salvage_invocations", 0),
        "prose_dispatches": counters.get("leaked_call_dispatches", 0),
        "sources": dict(sources),
        "parses": len(parses),
        "strict_ok": sum(1 for p in parses if p.get("strict_ok")),
        "round_trips": sum(d.get("round_trips", 1) for d in decodes),
        "decodes": len(decodes),
        "latency_ms": sum(d.get("latency_ms", 0) or 0 for d in decodes),
        "constrained_phases": sum(1 for p in phases if p.get("constrained")),
        "unconstrained_phases": sum(1 for p in phases if not p.get("constrained")),
        "fallbacks": sum(1 for d in decodes if d.get("fallback")),
        "clean": structural_failures == 0 and counters.get("salvage_invocations", 0) == 0,
    }


def aggregate(runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in runs:
        by_arm[r["arm"]].append(r)

    out: dict[str, dict[str, Any]] = {}
    for arm, rs in sorted(by_arm.items()):
        calls = sum(r["tool_calls"] for r in rs)
        failures = sum(r["structural_failures"] for r in rs)
        salvages = sum(r["salvages"] for r in rs)
        tasks = len(rs)
        clean = sum(1 for r in rs if r["clean"])
        sources: dict[str, int] = defaultdict(int)
        for r in rs:
            for k, v in r["sources"].items():
                sources[k] += v
        out[arm] = {
            "runs": tasks,
            "tool_calls": calls,
            "calls_per_run": round(calls / tasks, 2) if tasks else 0.0,
            "structural_failures": failures,
            "salvages": salvages,
            "fail_rate_per_call": round(100 * failures / calls, 2) if calls else 0.0,
            "clean_run_rate": round(100 * clean / tasks, 1) if tasks else 0.0,
            "prose_dispatches": sum(r["prose_dispatches"] for r in rs),
            "sources": dict(sources),
            "round_trips_per_decode": round(
                sum(r["round_trips"] for r in rs) / max(1, sum(r["decodes"] for r in rs)), 2
            ),
            "latency_ms_per_decode": round(
                sum(r["latency_ms"] for r in rs) / max(1, sum(r["decodes"] for r in rs)), 1
            ),
            "fallbacks": sum(r["fallbacks"] for r in rs),
            "constrained_phases": sum(r["constrained_phases"] for r in rs),
            "unconstrained_phases": sum(r["unconstrained_phases"] for r in rs),
        }
    return out


def projected_clean_task_rate(fail_rate_per_call: float, calls: float) -> float:
    """p^n — why a per-call rate is the wrong unit for an agent."""
    p = 1.0 - fail_rate_per_call / 100.0
    return 100.0 * math.pow(max(p, 0.0), max(calls, 0.0))


def render(agg: dict[str, dict[str, Any]]) -> str:
    lines: list[str] = []
    w = 78
    lines.append("=" * w)
    lines.append(f"{'arm':<16}{'runs':>6}{'calls':>7}{'fail/call':>11}"
                 f"{'clean runs':>12}{'prose':>7}{'rt/dec':>8}{'ms/dec':>9}")
    lines.append("-" * w)
    for arm, a in agg.items():
        lines.append(
            f"{arm:<16}{a['runs']:>6}{a['tool_calls']:>7}"
            f"{a['fail_rate_per_call']:>10.2f}%{a['clean_run_rate']:>11.1f}%"
            f"{a['prose_dispatches']:>7}{a['round_trips_per_decode']:>8.2f}"
            f"{a['latency_ms_per_decode']:>9.1f}"
        )
    lines.append("=" * w)

    lines.append("")
    lines.append("Compounding - a per-call rate is not a per-task rate:")
    for arm, a in agg.items():
        n = a["calls_per_run"]
        if n:
            proj = projected_clean_task_rate(a["fail_rate_per_call"], n)
            lines.append(
                f"  {arm:<16} {a['fail_rate_per_call']:.2f}% per call over "
                f"{n:.1f} calls -> {proj:.1f}% of tasks structurally clean "
                f"(observed {a['clean_run_rate']:.1f}%)"
            )

    lines.append("")
    lines.append("Dispatch surface - where each invocation came from:")
    for arm, a in agg.items():
        total = sum(a["sources"].values()) or 1
        parts = ", ".join(
            f"{k} {v} ({100*v/total:.0f}%)" for k, v in sorted(a["sources"].items())
        )
        lines.append(f"  {arm:<16} {parts or '(no calls)'}")
        if a["prose_dispatches"]:
            lines.append(
                f"  {'':16} ^ {a['prose_dispatches']} derived from the reasoning "
                "channel: attacker-reachable"
            )

    warn = [a for a in agg.values() if a["fallbacks"]]
    if warn:
        lines.append("")
        lines.append(f"WARNING: {sum(a['fallbacks'] for a in warn)} CSCD fallback(s) — "
                     "the server did not honour a constraint; check the spike.")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Summarise agent run traces")
    ap.add_argument("paths", nargs="+", type=Path, help="trace files or directories")
    ap.add_argument("--json", type=Path, default=None, help="also write JSON here")
    args = ap.parse_args()

    files = collect(args.paths)
    if not files:
        print("no .jsonl traces found", file=sys.stderr)
        return 2

    runs = []
    for f in files:
        try:
            runs.append(summarise_run(read_trace(f)))
        except Exception as e:  # a truncated trace should not stop the report
            print(f"skipping {f}: {type(e).__name__}: {e}", file=sys.stderr)

    if not runs:
        print("no readable runs", file=sys.stderr)
        return 2

    agg = aggregate(runs)
    print(f"{len(runs)} run(s) from {len(files)} file(s)\n")
    print(render(agg))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps({"runs": runs, "by_arm": agg}, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
