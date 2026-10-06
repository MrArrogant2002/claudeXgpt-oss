# Build plan — turning the local code agent into a conference paper

**Created:** 2026-10-01 · **Supersedes:** `proj-conf.md`, `docs/research/evaluation-plan.md`,
`docs/research/evaluation-and-trustworthiness.md`
**Companion:** [`docs/review/2026-09-30-audit-and-research-review.md`](../review/2026-09-30-audit-and-research-review.md) (the audit this plan executes)

**Locked decisions (2026-09-30):** ICDEC-2026 abandoned · framing is **on-premise / air-gapped
privacy + security** (no edge/TinyML claims) · spine is **both primaries** (channel-scoped
constrained decoding + the reliability/security trade-off) · hardware is an RTX A6000 48 GB
GPU box, development on a GPU-less laptop, synced over GitHub.

---

## 1. The paper this plan builds

### 1.1 Thesis

> In a local agentic coder driven by a small open-weight model, **structural reliability and
> security are in tension**. The repair heuristics that make a 20 B model usable as an agent —
> tolerant tool-call parsing, dispatching calls the model emits in the wrong channel, forced
> synthesis — widen the surface from which a tool invocation can be derived, until untrusted
> repository content can reach the dispatcher. We show that **channel-scoped constrained
> decoding** removes the structural failures those heuristics exist to repair, without the
> reasoning-quality or tool-call-suppression costs reported for global constraints, and that
> compiling the permission model into an **OS-enforced capability profile** closes the residual
> surface. We evaluate on a fully offline agent with seed-locked, reliability@k methodology.

### 1.2 Contributions

| | Contribution | Where it comes from |
|---|---|---|
| **C1** | A fully offline code agent with **client-side Harmony rendering over raw token IDs**, which is what makes token-level intervention possible at all | exists (`agent/harmony_codec.py`, `agent/inference.py`) — needs §4 instrumentation |
| **C2** | **Channel-scoped constrained decoding (CSCD)**: constrain only the machine-parsed region of a Harmony completion, with a per-recipient argument schema | **new — Phase 4** |
| **C3** | **Dispatch-surface analysis** + the measured reliability/security trade-off, and **capability-profile enforcement** that closes it | **new — Phase 6, 7c** |
| **C4** | **Evidence ledger** (objective grounding, no LLM judge) + seed-locked reliability@k protocol | **new — Phases 1, 5** |

### 1.3 Research questions

- **RQ1** — Under hand-rendered Harmony with unconstrained decoding, what is the rate and
  taxonomy of structural tool-call failures in gpt-oss-20b on repository tasks?
- **RQ2** — Does CSCD eliminate them, and at what cost in reasoning quality, tool-call
  suppression, latency and tokens, compared with global constrained decoding and with repair?
- **RQ3** — How large is the dispatch surface of each reliability mechanism, measured as
  indirect-prompt-injection success rate, and what utility is lost by removing each?
- **RQ4** — Does capability-profile enforcement reduce attack success to zero without reducing
  task success?
- **RQ5** — How much of the residual error is semantic rather than structural (i.e. does the
  scale-dependent semantic gap reproduce in an agentic setting)?

---

## 2. Method 1 — Channel-Scoped Constrained Decoding (C2)

### 2.1 The observation it rests on

A Harmony tool call is two syntactically rigid regions wrapped around one free region:

```
<|start|>assistant<|channel|>analysis<|message|>  …free-form reasoning…           <|end|>
<|start|>assistant<|channel|>commentary to=functions.read <|constrain|>json<|message|>{"path":"a.py","start_line":1}<|call|>
                            └──────────── HEADER (rigid) ────────────┘└──── ARGS (rigid, schema'd) ────┘
```

Every structural failure this project observes lives in the two rigid regions, never in the
free one:

| Failure | Region | Current treatment |
|---|---|---|
| duplicated recipient (`to=functions.read to=functions.read`) | header | regex salvage, `harmony_codec._lenient_parse` |
| unknown/typo'd channel name | header | silently produces a non-final, non-call message |
| invalid JSON arguments | args | `ERROR: invalid JSON arguments` fed back as data |
| wrong parameter name (`query` for `pattern`) | args | alias tolerance in `grep_tool`, `read_tool` |
| call emitted as prose in `analysis` | *no header at all* | `loop._infer_leaked_call` (the §3 problem) |

So: **do not constrain the whole generation. Constrain the two rigid regions only.** This is
the contribution, and it is available to this project specifically because it renders and
parses token IDs itself — a client on an OpenAI-compatible chat endpoint cannot intervene
mid-completion.

### 2.2 Two variants (implement both; CSCD-I is primary)

**CSCD-I — injected canonical header** *(primary — simpler and strictly stronger)*

1. **Phase A (free).** Decode unconstrained from the rendered prefill, pushing tokens through
   an incremental recognizer. Two exits: a completed `final` message → return; or the onset of
   a `commentary` channel header → go to Phase B.
2. **Phase B (recipient selection, constrained to the registry).** Issue one short
   `/completion` whose grammar admits exactly the registered tool names. The model has already
   decided semantically; we only force the spelling. One decode step in practice.
3. **Phase B′ (header injection).** The orchestrator **writes the canonical header token
   sequence itself** — `to=functions.<name> <|constrain|>json<|message|>` — rather than letting
   the model generate it. Malformed headers become *impossible by construction*, with no
   grammar needed over Harmony special tokens.
4. **Phase C (arguments, schema-constrained).** Issue `/completion` with prefill = tokens so
   far and `json_schema` = **that specific tool's** schema from the registry, terminated at
   `<|call|>`. Invalid JSON and wrong parameter names become impossible.

**CSCD-G — grammar-constrained header** *(ablation)*

Same, but Phase B uses a GBNF that accepts the full header form instead of injecting it. Keeps
the model "in control" of the header; measures whether injection costs anything. Needs Harmony
special tokens expressible in the grammar — validate early (§2.4).

### 2.3 Why this answers the known objections

- **Constraint tax / reasoning degradation.** The constraint is never active during `analysis`.
  The reasoning token distribution is untouched by construction, and we measure it rather than
  asserting it (§7.2 metric *reasoning-quality delta*).
- **Tool-call suppression.** The reported global-schema failure is that the compiled automaton
  forbids the token that *opens* a tool call, so the model stops calling tools entirely. CSCD
  activates only *after* the model has begun a commentary header, so suppression is structurally
  impossible. This is the sharpest argument in the paper — make it explicitly.
- **Schema tightness.** A union-of-tools schema must accept every tool's parameters, so it
  cannot reject `{"query": …}` for `read`. Per-recipient schemas can. CSCD is therefore
  *tighter* than global constraint, not merely cheaper.

### 2.4 Validation spike (do this first — half a day, before committing to Phase 4)

On the GPU box, confirm against the actual `llama-server` build:

1. `/completion` accepts `grammar` **and** `json_schema` alongside a **token-ID** `prompt`.
2. `cache_prompt: true` reuses the KV cache across the A→B→C hand-off (if not, CSCD costs ~3×
   prefill and the latency story changes — report it either way).
3. Whether GBNF can express Harmony special tokens (`<|constrain|>`, `<|call|>`). If **no**,
   CSCD-G is dropped and CSCD-I is the whole method — which is fine, and is why CSCD-I is
   primary.
4. `return_tokens` still returns IDs under a grammar.

**Write this spike up as `docs/spikes/cscd-feasibility.md` with the exact server build and
raw request/response bodies.** It is the paper's reproducibility anchor and it de-risks the
single assumption the method depends on.

---

## 3. Method 2 — Dispatch surface and capability enforcement (C3)

### 3.1 The formalisation

Define the **dispatch surface** `D(A)` of an agent `A` as the set of text regions from which
the orchestrator can derive a tool invocation. Then, for this codebase:

| Configuration | `D` | Reachable by an attacker? |
|---|---|---|
| strict parse only | valid Harmony commentary headers in model output | no |
| + salvage (`_lenient_parse`) | regex-matchable headers in model output | no (still model output only) |
| + leaked-call dispatch (`loop._infer_leaked_call`) | **any prose the model emits** | **yes** — models routinely quote what they read, so `D ⊇ {repo file contents} ∪ {command outputs}` |

The third row is the finding. The repair heuristic that recovers wasted turns on a weak model
is the one that connects untrusted input to the dispatcher. `_extract_json_obj` makes it worse
by scanning from the first `{` to the last `}`, so an embedded object in quoted text suffices;
and because `bash_tool` carries no `check_permissions`, `permissions.py:95` lets the resulting
`{"command": …}` through ungated.

**This is a trade-off curve, not a bug report.** Removing the heuristic costs utility on a weak
model (wasted turns, lower task success). The paper measures both axes and shows that CSCD makes
the trade-off unnecessary — because with structurally valid calls guaranteed, there is nothing
left for leaked-call dispatch to recover.

### 3.2 The enforcement

Compile the permission mode into a **capability profile** — `(fs_read, fs_write, net, exec)` —
and enforce it below the Python process rather than inside it:

- **Use `bubblewrap` (`bwrap`), not hand-rolled namespaces.** It gives unprivileged user
  namespaces, read-only bind mounts outside the project root, `--unshare-net`, and a clean
  `--die-with-parent` in one command line. Hand-rolling `unshare`/`seccomp` is weeks of work
  and reviewer-bait for subtle errors; `bwrap` is packaged on Ubuntu and auditable in a figure.
- **`--unshare-net` is what converts the air-gap from an assumption into a property.** Today the
  air-gap is an environmental claim; under a profile it is enforced, and that is a sentence the
  paper can actually defend.
- Give `bash_tool` a `check_permissions` so it resolves through `PermissionEngine` like the
  write tier, and record the decision in the trace.
- Keep a `none` profile (today's behaviour) as the experimental baseline arm — do **not** delete
  the unsafe path, it is the control.

### 3.3 Threat model to write down (currently absent — this is why §1.1 of the audit happened)

State explicitly: trusted = the user and their shell. Untrusted = **repository contents, command
output, dependency metadata, filenames**. Out of scope = a malicious model, a compromised
`llama-server`, hardware attacks. In scope = indirect prompt injection via any untrusted
channel, path escape, unauthorised write, and egress. Name the fact that `AGENT_BASE_URL` is
plain HTTP to a second machine and say what that means for the privacy claim.

---

## 4. Instrument — evidence ledger and reproducibility (C4)

### 4.1 Evidence ledger

Per observation, append `(obs_id, tool, path, line_range, sha256(content), turn, run_id)`.
Properties that matter:

- **Compaction-immune.** The ledger is re-rendered as a compact developer-message table each
  turn; it is never part of the summarisable history. This directly addresses the documented
  failure where compaction silently erases the evidence a constraint or observation depended on.
- **Citable.** The final answer must cite `obs_id`s. A post-hoc checker re-reads each cited
  range and verifies the hash.
- **Objective.** Grounding is verified mechanically. No LLM judge — which matters because an
  LLM judge would either need a cloud model (destroying the offline premise) or the model under
  test (circular).

Derived metrics: citation rate, verified-citation rate, ungrounded-claim rate, and
**evidence-decay rate** (asserted a check passed when the observation of its failure had been
compacted away).

### 4.2 Reproducibility protocol

- `seed` sent on every `/completion` and logged; greedy mode (`temperature=0, top_k=1`)
  available as an arm.
- Single server slot (`--parallel 1`) — batch composition is the dominant source of numerical
  nondeterminism, so this is not optional.
- Every dependency pinned with `==`. **`openai-harmony` is the renderer: a minor bump changes
  the prompt bytes, hence the results.** Highest-leverage one-line change in the repo.
- Per-trial **run manifest**: git SHA, GGUF sha256, llama.cpp commit, `n_ctx`, KV type,
  sampling, seed, registry contents, grep backend, capability profile, decoding arm.
- **JSONL trace** per run — one record per event. Everything in §7 is computed from these files
  and nothing else.
- **reliability@k** (fraction of k seeds that succeed) reported alongside means, with 95 % CIs.
  Single-run numbers from a stochastic loop are not results.
- Publish one bitwise-determinism check as a sanity artifact.

---

## 5. What changes in the repository, and why

Ordered as it should be executed. Every phase has an acceptance criterion; nothing downstream
starts until the criterion is met.

### Phase 0 — Repo hygiene (0.5 day)

| Change | Why |
|---|---|
| Remove superseded research docs (list and open question in §9) | They encode the abandoned ICDEC/edge framing and the "reliability layer as novelty" claim the audit retired. Leaving them creates two contradictory plans in one repo |
| Fold the §8 bibliography of `project-construction-notes.md` into §8 of this file | Those classic citations are real and needed; losing them to a `git rm` would be a self-inflicted wound |
| Commit the already-deleted `docs/research/literature-survey.xlsx` | The tree has been dirty since before this review |
| Fix `README.md`'s dead link to `research-paper/` | It points at nothing |
| Resolve the `NIMBUS_*` leftovers in `shell_session.py` | They will appear in a paper listing |

*Acceptance:* clean `git status`, no dead links, one plan in the repo.

### Phase 1 — Determinism and observability (2 days)

| Change | Why |
|---|---|
| `seed` in `inference._sampling()`; greedy mode flag | **No run is currently reproducible.** Blocks every number in the paper |
| Pin all dependencies with `==` | See §4.2 |
| `agent/trace.py` — JSONL writer, one record per event, `run_id` + manifest header | The only record of a run today is ANSI text scrolling past. Nothing can be aggregated |
| Replace module-global `USAGE` / `SALVAGE_COUNT` with a per-run counter object | Process-wide globals cross-contaminate any in-process sweep |
| `AgentSettings` frozen settings object; remove runtime mutation of `config` | Two configurations cannot coexist in one process today, so a sweep driver is impossible. Also gives one object to serialise into the manifest |

*Acceptance:* two runs with the same seed and manifest produce identical traces; a sweep script
can instantiate two differently-configured agents in one process.

### Phase 2 — Fixes that would otherwise confound measurement (2 days)

| Change | Why |
|---|---|
| Freeze `grep` semantics across both backends; record which ran | ripgrep respects `.gitignore` and skips hidden files, the Python fallback does neither. Today the same task on two machines is two different experiments |
| Stream `rg` output via `Popen`, kill at `max_matches`, timeout from config | `capture_output` buffers all of stdout before the cap applies |
| Delete the analysis-as-answer fallback (`loop.py:191`) | Returning the longest `analysis` message as the answer emits exactly the text the model declined to commit to. Fatal to a faithfulness measurement, and to the thesis |
| Compaction: compact on turn boundaries, preserve call/result pairing, inject summary as **developer** not **user**, add a cooldown | Can currently orphan a tool result from its call, fabricates user turns, and re-summarises every turn when summarisation fails |
| Nudges become developer-role messages | Same fabrication problem; also contaminates "what the user asked" attribution |
| `read`: size guard + range-tracked `record_read` | Loads whole files into memory; and hashing the full file lets `edit` proceed on a file the model largely has not seen, which is weaker than the documented invariant |
| Scope `_SENSITIVE_PREFIXES` / make the `vendor` rule configurable | `secrets.py` and any Go/PHP `vendor/` tree are permanently unwritable in every mode |
| Streaming display derived from the authoritative parse | What the user watched can differ from what was logged |

*Acceptance:* a regression suite covering each of the above; grep results identical with and
without `rg` on PATH.

### Phase 3 — Turn state machine (3 days)

Decompose `loop.run_turn` (≈200 lines, 13 parameters, six concerns) into an explicit state
machine plus `RecoveryPolicy` objects with `applies(state)` / `apply(state)`.

**Why this is method work, not cleanup:** an experimental arm becomes *a list of enabled policy
objects*, so the §7 ablation table is a literal configuration rather than a thicket of env
flags. It is also the only way the recovery mechanisms become unit-testable without a live
server.

*Acceptance:* every arm in §7.1 expressible as a config; `run_turn` behaviour unchanged on the
Phase 2 regression suite.

### Phase 4 — CSCD (5 days, after the §2.4 spike)

New `agent/decoding/` with: an incremental Harmony-region recognizer; `cscd_injected.py`
(CSCD-I); `cscd_grammar.py` (CSCD-G, if the spike allows); `schema_to_gbnf.py` or the
`json_schema` pass-through; and a `DecodingStrategy` interface so `unconstrained`,
`global_schema`, `cscd_i`, `cscd_g` are swappable arms.

*Acceptance:* on a fixed 30-task smoke set, `cscd_i` yields a **0 %** strict-parse failure rate
and a 0 % schema-violation rate; tool-call rate per task is not lower than `unconstrained`
(no suppression); latency overhead measured and reported.

### Phase 5 — Evidence ledger (2 days)

`agent/ledger.py` + a `verify_citations` checker + the developer-message renderer. Build it
**alongside Phase 4, not after** — it is the faithfulness instrument for every arm, and adding
it later means re-running everything.

*Acceptance:* ledger survives a forced compaction; the checker detects a deliberately corrupted
citation.

### Phase 6 — Capability profiles (4 days)

`agent/containment/` with profile definitions, a `bwrap` launcher, `bash_tool` routed through
`PermissionEngine`, and `profile ∈ {none, denylist, enforced}` as an experimental arm.
Leaked-call inference refused for any `read_only=False` tool, and leaked JSON required to be
the entire message rather than an embedded substring.

*Acceptance:* under `enforced`, a canary file outside the project root is unreadable and
unwritable from `bash`; a local sink server is unreachable; task success on the Phase 4 smoke
set is unchanged.

### Phase 7 — Benchmarks (3 weeks — the real cost)

- **7a. Repo-QA suite** (~60 questions, 3–4 small OSS repos, different languages) with
  **file/line ground truth** for every answer. This is what makes grounding objective and it is
  unavoidable hand labour. Budget the most time here.
- **7b. SWE-bench Verified subset** (50 instances) via the official harness, for a task-success
  number reviewers recognise.
- **7c. Injection suite** (~40 scenarios) across four vectors — repo file content, command
  output, dependency metadata, filename — and five payload classes: instruction-style,
  JSON-payload (targets leaked-call dispatch), path-escape, egress, credential read. Each with a
  machine-checkable oracle (canary file, local sink, trace assertion).

*Acceptance:* every suite runs headless from a single command and writes JSONL; a deliberately
broken agent fails the suites.

### Phase 8 — Experiments and analysis (2 weeks)

Arms, k, and the aggregate design are in §7. Produce `reports/*.json` → tables and figures via
a committed script, never by hand.

### Phase 9 — Draft (3 weeks, overlapping Phase 8)

LaTeX, `references.bib` separate from the manuscript, every figure a `\begin{figure}` with a
`% TODO(diagram):` note. Template depends on the venue (open question, §9). Prose written by
the author; this plan and the traces are the inputs.

---

## 6. Proposed file layout after the build

```
agent/
  settings.py          NEW  frozen AgentSettings (replaces mutable config globals)
  trace.py             NEW  JSONL event writer + run manifest
  ledger.py            NEW  evidence ledger + citation verifier
  turn/                NEW  state machine + RecoveryPolicy objects (was loop.run_turn)
  decoding/            NEW  DecodingStrategy: unconstrained | global_schema | cscd_i | cscd_g
  containment/         NEW  capability profiles + bwrap launcher
  harmony_codec.py          + region recognizer for CSCD phase detection
  inference.py              + seed, grammar/json_schema pass-through, per-run counters
  tools/bash_tool.py        + check_permissions, profile-aware launch
eval/
  suites/repo_qa/      NEW  questions + line-level ground truth
  suites/swebench/     NEW  subset runner
  suites/injection/    NEW  scenarios + oracles
  runner.py            NEW  sweep driver (arms × seeds), writes reports/*.json
  analysis/            NEW  tables + figures from traces
tests/                 NEW  sandbox, permissions, parser, leaked-call, atomic_write, shell
docs/
  plans/conference-paper-build-plan.md   this file
  review/2026-09-30-audit-...md          the audit
  spikes/cscd-feasibility.md             NEW  the §2.4 spike writeup
  reference/gpt-oss-harmony.md           KEEP (moved) — required by Phase 4
  architecture/                          KEEP — paper figures
```

---

## 7. Experiment design

### 7.1 Arms

**Decoding** (suites 7a, 7b): `unconstrained` · `unconstrained + repair` (today's system) ·
`global_schema` · `cscd_i` · `cscd_g`.
**Containment** (suite 7c): `none` · `denylist` · `enforced`, crossed with
`repair ∈ {off, salvage, +leaked-call}`.

Do not run the full cross product. Decoding arms on 7a/7b; containment × repair on 7c; one
`cscd_i + enforced` cell on all three suites as the proposed configuration.

### 7.2 Metrics

*Structural:* strict-parse failure rate · salvage invocations · schema-violation rate ·
leaked-call dispatches · **tool-call rate per task** (suppression detector).
*Semantic:* correct-tool selection · correct-argument rate · **reasoning-quality delta** across
decoding arms (this is the RQ5 instrument — the scale-dependent semantic gap).
*Grounding:* citation rate · verified-citation rate · ungrounded-claim rate · evidence-decay rate.
*Task:* success / reliability@k · turns-to-completion · recovery count.
*Security:* attack success rate per vector and payload class · utility delta from removing each
heuristic.
*Cost:* prompt and output tokens per task · wall-clock · CSCD round-trip overhead.

### 7.3 Baselines

1. The same GGUF driven by `llama-server --jinja` native tool-call templating — the
   off-the-shelf comparison.
2. One established open agent harness on the same weights — so the comparison is not against
   your own earlier commit.
3. A larger open-weight model that fits 48 GB, labelled explicitly as a **capability ceiling,
   not a competitor**.

### 7.4 Protocol

k = 5 seeds · fixed sampling · `--parallel 1` · manifest per trial · all traces archived ·
per-arm aggregates with 95 % CIs · **every negative result reported**, specifically: suppression
under global constraints, semantic errors constraints cannot fix, and any case where repair beat
CSCD.

---

## 8. Bibliography

### 8.1 Carried forward from `project-construction-notes.md` §8 (verified classics)

*Agent loop:* ReAct — Yao et al. 2022 (arXiv:2210.03629) · Chain-of-Thought — Wei et al. 2022
(arXiv:2201.11903) · Reflexion — Shinn et al. 2023 (arXiv:2303.11366) · Self-Refine — Madaan et
al. 2023 (arXiv:2303.17651).
*Tool use:* Toolformer — Schick et al. 2023 (arXiv:2302.04761) · ToolLLM — Qin et al. 2023
(arXiv:2307.16789) · Gorilla — Patil et al. 2023 (arXiv:2305.15334), plus the Berkeley
Function-Calling Leaderboard.
*Code agents & benchmarks:* SWE-bench — Jimenez et al. 2023 (arXiv:2310.06770) · SWE-agent —
Yang et al. 2024 (arXiv:2405.15793) · CodeAct — Wang et al. 2024 (arXiv:2402.01030) ·
Codex/HumanEval — Chen et al. 2021 (arXiv:2107.03374) · MBPP — Austin et al. 2021
(arXiv:2108.07732).
*Retrieval contrast:* RAG — Lewis et al. 2020 (arXiv:2005.11401).
*Model:* gpt-oss model card (arXiv:2508.10925) · MX / MXFP4 — Rouhani et al. 2023
(arXiv:2310.10537) · Mixtral — Jiang et al. 2024 (arXiv:2401.04088).
*Evaluation:* SelfCheckGPT — Manakul et al. 2023 (arXiv:2303.08896) · G-Eval — Liu et al. 2023
(arXiv:2303.16634) · RAGAS — Es et al. 2023 (arXiv:2309.15217) · TrustLLM — Sun et al. 2024
(arXiv:2401.05561).

### 8.2 New, from the 2026 search (2026-09-30)

**Verify every ID, title and number against the actual PDF before citing.** Only the first was
fetched and read in full; the rest come from search snippets, which are not evidence.

*Constrained decoding — the core related work for C2:*
- Constrained Decoding Eliminates Structural Failures in Small LLMs but Reveals a
  Scale-Dependent Semantic Gap — arXiv:2609.23742 **(read; grounds RQ5)**
- Repair, Not Improvement: Decomposing Constrained Decoding in Tool-Call Abstention —
  arXiv:2608.13959 **(closest prior work — read first)**
- Constraint Tax in Open-Weight LLMs: Tool Calling Suppression Under Structured Output
  Constraints — arXiv:2606.25605 **(the objection CSCD answers — read second)**
- When Correct Isn't Usable: Structured Output Reliability in Small Language Models —
  arXiv:2605.02363
- AdapTrack: Constrained Decoding without Distorting LLM's Output Intent — arXiv:2510.17376
- Learning CFGs for Grammar-Constrained Decoding — arXiv:2608.05493

*Agent security — the core related work for C3:*
- The Balkanization of Execution-Security Research for AI Coding Agents: Isolation, Access
  Control, and TOCTOU — arXiv:2607.05743 **(names the sandbox race in `agent/sandbox.py`)**
- Permission Denied: Policy-Graded Evaluation of Coding Agents in Hardened Environments —
  arXiv:2608.02670 **(the evaluation shape for RQ4)**
- Before the Tool Call: Deterministic Pre-Action Authorization for Autonomous AI Agents —
  arXiv:2603.20953
- LivePI: Realistic Benchmarking of Agents Against Indirect Prompt Injection — arXiv:2605.17986
- GitInject: Prompt Injection in AI-Powered CI/CD — arXiv:2606.09935
- NetInjectBench — arXiv:2607.10490 · ToolPrivacyBench — arXiv:2606.28061
- LongPIBench — arXiv:2608.28411 · Adaptive Evaluation of Out-of-Band Defenses —
  arXiv:2606.26479

*Context management — grounds the evidence ledger:*
- Governance Decay: How Context Compaction Silently Erases Safety Constraints in Long-Horizon
  LLM Agents — arXiv:2606.22528 **(the mechanism the ledger defends against)**
- ACON: Optimizing Context Compression for Long-horizon LLM Agents (Microsoft Research)
- Addressable Recall Compaction — arXiv:2607.25066 · What Does Context Compression Cost an
  Agent? — arXiv:2608.16370 · CliffCompaction — arXiv:2609.26779 · Toward Reliable Context
  Compression: Execution Instability — arXiv:2608.06503

*Repository context — supports the retrieval-free design as background, not headline:*
- Agent Retrieval Bench — arXiv:2607.24882 · CORE-Bench — arXiv:2606.11864 · Deep Agentic
  Search for Repository-Level Code QA — arXiv:2608.01507 · RAG for Code: Survey —
  arXiv:2510.04905 · Partial Dependency Graph Context Retrieval — arXiv:2608.01927

*Reproducibility:*
- Understanding and Mitigating Numerical Sources of Nondeterminism — arXiv:2506.09501
- Accelerating the Mitigation of LLM Inference Nondeterminism — arXiv:2609.25624

*Platform:* In harmony with gpt-oss — arXiv:2604.00362 · https://huggingface.co/openai/gpt-oss-20b

### 8.3 Reading order before drafting related work

`2608.13959` → `2606.25605` → `2609.23742` (establishes the CSCD gap) · then `2608.02670` →
`2605.17986` → `2607.05743` (establishes the C3 gap) · then `2606.22528` (the ledger's framing).

---

## 9. Timeline

| Week | Work | Gate |
|---|---|---|
| 1 | §2.4 CSCD spike · Phase 0 · Phase 1 | **Spike result decides CSCD-G in/out** |
| 2 | Phase 2 · tests for Phases 1–2 | Reproducible, un-confounded baseline |
| 3 | Phase 3 (state machine) | Arms are configuration |
| 4–5 | Phase 4 (CSCD) + Phase 5 (ledger) | 0 % structural failure on the smoke set |
| 6 | Phase 7a begins (repo-QA labelling) | — |
| 7 | Phase 6 (containment) · 7a continues | Canary unreachable under `enforced` |
| 8 | Phase 7b, 7c · pilot runs | Suites run headless |
| 9 | **Decoding-arm runs → §5.1 results** | **arXiv preprint submitted** |
| 10–11 | Containment × repair runs → C3 results | Trade-off curve in hand |
| 12–14 | Analysis, figures, full draft, integrity check | Submission-ready |

Preprint at week 9 is deliberate and should not slip: channel-scoped constrained decoding is a
cheap, attractive idea in a dense area, and the preprint is what establishes priority. Do not
hold it for the security half.

---

## 10. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| `llama-server` rejects `grammar` + token-ID prompt, or breaks `cache_prompt` | medium | §2.4 spike in week 1; CSCD-I needs only `json_schema`; worst case, segment with explicit prefill and report the 3× prefill cost honestly |
| GBNF cannot express Harmony special tokens | medium | CSCD-I needs no special tokens in the grammar — it injects them. CSCD-G simply drops out as an ablation |
| Repo-QA labelling overruns | **high** | Hard-cap at 40 questions over 3 repos; it is the long pole. Start week 6, not week 10 |
| SWE-bench harness setup on an air-gapped box | medium | Pre-pull all images on the GPU box in week 7; if infeasible, drop 7b and lean on 7a + reliability@k, stating the external-validity limitation |
| Scooped on CSCD | medium | Week-9 preprint, no exceptions |
| `bwrap` unavailable / user namespaces disabled on the box | low–medium | Check in week 1 alongside the spike; fall back to a dedicated unprivileged user + mount policy and scope the claim to that |
| §5.2 numbers come out uninteresting | low | The §5.1 + reproducibility paper stands alone. Keep the decoding arms independent of the containment code so this fallback stays live |

---

## 11. Integrity notes

- Prose authored by the author; this plan, the traces, and `reports/*.json` are the inputs.
- Never report a number that is not in a committed trace file.
- Report the negatives named in §7.4 — a trade-off paper is *stronger* with honest failure modes,
  and weaker if the trade-off only ever favours the proposed method.
- The artifact release should include the eval suites, the traces, and the manifests. Decide
  before release what to do about `docs/research/claude-internal-structure.md` (see §12).

---

## 12. Open questions for the author

1. **Deletion scope** — which files count as "the old research files" (§9 of this plan proposes
   a specific list).
2. **`docs/research/claude-internal-structure.md`** — 4,000 lines distilled from a
   reverse-engineering of Anthropic's Claude Code CLI. It is useful design reference (context
   management, sub-agents, concurrent tools) *and* an awkward thing to ship inside the artifact
   of a paper that describes a "Claude Code–style" agent. Recommend: keep locally, exclude from
   the released artifact, never cite. Confirm.
3. **LaTeX template** — standing instruction is IEEEtran, but an ASE/FSE submission needs ACM
   `acmart`. Scaffold which?
4. **SWE-bench (7b)** — in or out? It is the number reviewers recognise and the single largest
   infrastructure cost on an offline box.
