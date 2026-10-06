# Implementation status

Tracks [`conference-paper-build-plan.md`](conference-paper-build-plan.md).
Updated 2026-10-05. **45 tests pass, 1 skipped** (`.venv/Scripts/python -m pytest`).

## The headline result from this session

**CSCD-I is implementable, and the one assumption it rested on is confirmed.**
`HarmonyEncoding.encode(text, allowed_special="all")` round-trips Harmony special
tokens exactly, so the orchestrator can emit a canonical header itself. Header
injection is therefore not a hopeful design — it works, and
`tests/test_strategies.py` proves the guarantee is structural rather than
probabilistic: given a replayed completion containing a duplicated recipient, an
unregistered tool and non-JSON arguments, the assembled output contains exactly
one well-formed recipient and none of the garbage.

What is *not* yet confirmed is whether the local `llama-server` honours
`grammar` / `json_schema` alongside a token-ID prompt. That is what
`scripts/spike_cscd.py` establishes, and it must run on the GPU box before any
measurement.

## Built and tested

| Phase | Component | File | Tests |
|---|---|---|---|
| 1 | Immutable settings; experiment arms as configuration | `agent/settings.py` | 5 |
| 1 | JSONL trace, run manifest, per-run counters | `agent/trace.py` | 5 |
| 1 | Seed, `extra_body`, per-run counters on the client | `agent/inference.py`, `agent/config.py` | — |
| 1 | Dependencies pinned exactly | `requirements.txt` | — |
| 4 | Harmony special tokens resolved from the live encoding | `agent/decoding/tokens.py` | — |
| 4 | Incremental region recognizer | `agent/decoding/recognizer.py` | 9 |
| 4 | Tool-name grammar, per-recipient and union schemas | `agent/decoding/gbnf.py` | 6 |
| 4 | Transport + `ReplayClient` (GPU-free testing) | `agent/decoding/client.py` | — |
| 4 | Four decoding arms | `agent/decoding/strategies.py` | 10 |
| 5 | Evidence ledger + citation verifier | `agent/ledger.py` | 6 |
| 6 | Capability profiles + bubblewrap launcher | `agent/containment.py` | 4 |
| §2.4 | Server feasibility spike | `scripts/spike_cscd.py` | run on the box |

Two bugs were found by the tests while writing them, both of which would have
produced silently wrong paper numbers:

* the ledger hashed observed text with its trailing newline but verified against
  a slice reconstructed without one, so **every citation would have reported as
  stale** and the grounding metric would have read zero;
* `tool_name_grammar([])` on an empty registry would have constrained the decode
  to the empty language, which hangs rather than fails. It now raises.

## Not yet done

The components exist and are tested in isolation. **`loop.py` still runs the old
path**, so nothing below is reachable from the TUI yet. This is the largest
remaining chunk and it is wiring, not design.

| Phase | Remaining | Why it matters |
|---|---|---|
| 2 | Freeze grep semantics across backends; drop the analysis-as-answer fallback (`loop.py:191`); pairing-preserving compaction; developer-role nudges; `read` size guard and range-tracked freshness; narrow the sensitive-path prefixes | Each is a measurement confound; the grep one makes two machines two different experiments |
| 3 | Turn state machine + `RecoveryPolicy` objects | Until this lands, an ablation arm is an `if`, not a configuration |
| 4 | Wire `DecodingStrategy` into the turn loop | CSCD is unreachable from the agent |
| 5 | Wire the ledger into `read`/`grep` and the final-answer prompt | Grounding is not yet measured |
| 6 | Give `bash` a `check_permissions`; route execution through `wrap_argv` | **The permission bypass is still open.** `permissions.py:95` still returns allow for `bash` |
| 7 | Repository-QA suite (~40 questions, line-level ground truth); issue-resolution subset; injection suite | The long pole — start it before the code is finished |
| 8 | Sweep driver and analysis | — |

## Run it

```bash
.venv/Scripts/python -m pytest                      # 45 passed, 1 skipped
python scripts/spike_cscd.py --base-url http://localhost:8081   # on the GPU box
```

The skipped test is the bubblewrap launcher, which needs Linux. It will run on
the GPU box and is the gate for the `enforced` containment arm.

## Decisions taken while implementing

**`bubblewrap` over hand-rolled namespaces.** Packaged, unprivileged, auditable
in a figure. A hand-rolled `unshare`+`seccomp` equivalent is weeks of work and
invites subtle errors a reviewer cannot check.

**An unavailable `enforced` profile raises instead of degrading.** An arm
labelled "enforced" that silently ran unconfined would invalidate the security
results, so `wrap_argv` refuses rather than continuing.

**Traces hash tool arguments and results rather than storing them.** A trace of a
private repository is itself a disclosure risk, and the metrics only need
identity. `tests/test_foundation.py` asserts a secret path never reaches the
trace file.

**`tool_call(source=...)` records where an invocation was derived from** —
`header`, `salvage`, or `prose`. That field *is* the dispatch-surface
measurement; nothing else recovers it after the fact.

**The `none` containment profile is retained deliberately.** It reproduces
today's unconfined behaviour and is the control arm of the security experiment,
not dead code.
