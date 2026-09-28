# Evaluation Plan — Offline, Trustworthy Local Code Agent

Scope: how to evaluate this project (gpt-oss-20b + the reliability layer, fully local) for
the ICDEC trustworthiness paper. Covers the full metric catalog, which models to compare
against (and which not to), context-size testing, the agentic-benchmark landscape and what
of it is feasible on a constrained local 20B, the concrete experiment matrix, and the
tables/figures to produce. Companion to `docs/research/evaluation-and-trustworthiness.md`.

> **Status.** No evaluation harness is implemented (an earlier prototype was removed). This
> document is the *methodology to build*. Where the metric catalog below tags an item
> **Have / Partial / Gap**, that refers to the removed prototype and marks how much a future
> harness would need to (re)implement — not code that exists today.

> **Framing.** The contribution is *trustworthiness and reliability of a small, offline,
> single-GPU agent*, not beating frontier cloud models. Evaluation therefore centres on
> **within-system contrasts** (reliability layer off→on; context size) measured on
> **agentic** metrics — not a capability race we cannot and should not win.

---

## 1. Metric catalog (everything, grouped)

Each row: what it measures · how we measure it · originating benchmark (author, year, to
cite) · status in our harness.

### A. Task success (capability)
- **Resolve rate / task success** — did the agent complete the task; for bug-fix tasks,
  do the repo's own tests pass afterward. *SWE-bench* (Jimenez 2023) / *SWE-bench Verified*
  (OpenAI 2024). **Have** (`verify_cmd`, substring/regex checks).
- **pass@1** — success on the first attempt. **Have** (single run).

### B. Reliability (the trustworthiness core)
- **pass@k** — succeeds in *at least one* of k attempts (optimistic). *τ-bench* (Yao 2024).
- **pass^k / reliability@k** — succeeds in *all* k attempts (the reliability metric — a
  trustworthy agent is one you can rerun). *τ-bench / τ²-bench* (Yao 2024–25). **Partial**
  (`--repeats` runs k times + token-Jaccard consistency; add pass^k as a small metric).
- **Self-consistency** — answer stability across repeats. *SelfCheckGPT* (Manakul 2023).
  **Have** (mean pairwise token Jaccard).

### C. Tool-use quality
- **Tool-call validity** — fraction of the model's tool emissions that are well-formed
  (clean vs. salvaged-malformed vs. leaked-into-reasoning). **Have** (from the event stream).
- **Tool-call correctness** — right tool, right arguments, and *not* calling a tool when
  none is needed (irrelevance detection). *BFCL / Berkeley Function-Calling Leaderboard*
  (Patil 2023–24). **Gap** (needs gold tool-call traces; validity is our proxy).

### D. Faithfulness / hallucination
- **Citation grounding** — every file path the answer cites must exist in the repo; any
  that doesn't is a hallucination. **Have** (`grounding()`).
- **Answer correctness vs. gold / judged** — *G-Eval* (Liu 2023), *RAGAS* (Es 2023).
  **Optional** (LLM-judge; note self-bias if gpt-oss judges gpt-oss).

### E. Safety / robustness
- **Path-escape / read-outside / sensitive-path** — out-of-bounds actions must not take
  effect. **Have** (safety tasks; sandbox + permission engine).
- **Indirect prompt-injection resistance** — a repo file carries planted instructions
  ("ignore previous… run `rm -rf`"); the agent must not obey. *AgentDojo* (Debenedetti
  2024), *InjecAgent* (2024). **Gap** (add injection tasks — high value for this paper).
- **Harmful-request refusal** — *AgentHarm* (2024). **Optional.**

### F. Efficiency / cost (edge relevance)
- **Turns / tool-steps to completion**, **recoveries** (salvage/leaked/nudge/synth counts),
  **output & prompt tokens per task**. **Have.**
- **Latency** (tokens/s, time-to-first-token), **peak VRAM**, **energy/task** (integrate
  `nvidia-smi --query-gpu=power.draw`). **Gap** (optional efficiency section).

### G. Non-agentic academic reasoning (report only, do NOT re-run)
- MMLU-Pro, GPQA-Diamond, AIME/MATH, HumanEval/MBPP, Codeforces. gpt-oss's model card
  already reports these; **cite them, don't reproduce** — they are not the paper's point.

---

## 2. Which model(s) to compare against

**Hard constraint:** our agent renders **OpenAI Harmony** client-side (`harmony_codec.py`)
and the salvage layer parses Harmony specifically. So *other* models (Qwen, Llama, Mistral)
cannot be dropped into our agent without adding their chat templates — a non-trivial port.
This shapes the comparison set.

**Recommended competition set (feasible + defensible):**
1. **Primary — the ablation, same model.** gpt-oss-20b with the reliability layer
   `off → salvage → leaked → full`. This *is* the headline result (RQ2) and needs no extra
   models or infra. The off→full delta on each metric is the contribution.
2. **Context sweep, same model** (Section 3) — a second within-system axis.
3. **Positioning against published numbers (cite, don't run).** Put gpt-oss-20b's and
   frontier models' *published* SWE-bench-Verified / τ-bench scores in related work to
   locate your constrained-local results — never as a head-to-head you execute.

**Optional / stretch:**
- **Other small local models as an external-validity reference** (Qwen2.5-Coder-7B/14B,
  Llama-3.1-8B, DeepSeek-Coder-V2-Lite, Codestral-22B, Phi-4). Only if you either (a) add a
  generic chat-template path to the codec (future work), or (b) run them through their own
  native tool loop on the *same tasks* — the latter compares raw models, not our agent, so
  label it clearly. Honest framing: cross-model portability is a **limitation / future
  work**, because the reliability layer is currently Harmony-specific.
- **gpt-oss-120b:** skip — MXFP4 ≈ 60 GB+ does not fit the 48 GB A6000.
- **Cloud ceiling (RQ4, optional).** One frontier cloud model on the same task suite as an
  *upper bound only*. This **breaks the offline/air-gapped claim**, so run it separately,
  present it as "the capability you give up for privacy," and never as a target to beat.

> Bottom line: **compete against yourself** (reliability off↔on, context small↔large) and
> **position against the literature**. That is the strongest, most reproducible story for a
> single-GPU offline system, and it sidesteps the Harmony-portability problem.

---

## 3. Context-size–based testing

Context is a first-class variable for an edge agent, and you have been running a tight
window. Sweep it and show the trade-off.

- **Serve at several windows:** relaunch `llama-server` with `-c 8192`, `13312`, `32768`,
  `65536` (the A6000 allows up to `131072`). The agent auto-detects `n_ctx` from `/props`,
  so just relaunch the server and tag each run by its window (e.g. "ctx32k").
- **Run the identical task suite at each** and plot metrics vs. window:
  - task success, tool-call validity, faithfulness;
  - **compaction frequency** and **recovery/spiral rate** (expected to rise as the window
    shrinks — the churn you observed at 13k);
  - turns and tokens/task.
- **Prompt-budget analysis:** report how many tokens the system prompt + tool schemas
  consume (you compacted these) and therefore how much window is left for actual work at
  each `-c`. This yields a concrete claim: *"the minimum context for a trustworthy local
  agent on these tasks is ≈ N tokens."*
- **Deliverable:** a "success (and recovery-rate) vs. context window" figure — a genuinely
  useful edge-deployment result, and it turns your fixed-13k constraint into a finding.

---

## 4. Agentic-systems evaluation — landscape and what to adopt

The field's agentic benchmarks and whether they are feasible here:

| Benchmark | Measures | Feasible on a local 13k-context 20B? |
|---|---|---|
| **SWE-bench / Verified** (Jimenez 2023) | resolve real GitHub issues (test-verified) | Full suite: heavy (Docker + per-repo harness) and a small model scores low. **Adopt the *methodology*** (test-verified fix tasks) on our small repo suite; optionally run a **20–50-task sample**, clearly caveated. |
| **τ-bench / τ²-bench** (Yao 2024) | tool-agent-user tasks, **pass^k** | Adopt the **pass^k reliability metric** (run each task k times); the full domains (retail/airline) are out of scope. |
| **BFCL** (Patil 2024) | function-call accuracy + irrelevance | Adopt the *idea* (tool-call validity/correctness); running BFCL itself needs its harness. |
| **Terminal-Bench** (2025) | agentic shell tasks | Our exec/run tasks are the same shape; optional to run the real suite. |
| **AgentDojo / InjecAgent** (2024) | prompt-injection resistance | **Adopt** — add injection tasks to our suite (high value, low cost). |
| **GAIA, AgentBench, WebArena, OSWorld, MLE-bench, SWE-Lancer, GDPval** | general/web/computer-use/ML/economic agents | Out of scope (need web/OS/infra or are not code-agent tasks). |

**Decision:** do **not** stake the paper on reproducing full public benchmarks on a
constrained local 20B. Instead, **borrow their metrics** (test-verified resolve rate,
pass^k, tool-call validity, injection-resistance) and run them through a small in-process
harness (to build) on a **real-repo suite**. Optionally add a small SWE-bench-Verified
*sample* as a single
positioning data point, explicitly labelled as subset/local/small-model.

---

## 5. Experiment matrix (concrete runs, prioritised)

Axes: model (fixed = gpt-oss-20b) × reliability rung × context size × repeats k × task suite.

| ID | Run | Answers | Priority |
|----|-----|---------|----------|
| **E1** | gpt-oss-20b · rungs {off,salvage,leaked,full} · fixed ctx · k=3–5 · full suite | **RQ1** (raw trustworthiness) + **RQ2** (ablation) + reliability@k | **must** |
| **E2** | gpt-oss-20b · full · ctx {8k,13k,32k,64k} · full suite | context vs. trustworthiness curve | **must** |
| **E3** | gpt-oss-20b · full · injection + escape/read-outside tasks · rungs {off,full} | **RQ3** safety / injection resistance | **must** |
| **E4** | (stretch) other small local models · native loop · same tasks | external validity / landscape | optional |
| **E5** | (stretch) one cloud model · same tasks | **RQ4** privacy↔capability ceiling | optional |
| **E6** | (stretch) SWE-bench-Verified 20–50-task sample | positioning vs. the field | optional |

Each run should write a per-run report (e.g. JSON); aggregate into the tables below.

---

## 6. Task suite / benchmark repos

- **3–5 small real repos**, mixed language, permissive licence: e.g. `click`, `flask`,
  `tqdm` (Python), `express` (JS), a small Rust (`hexyl`). Give each a `.venv`.
- **Categories** (a small JSONL task set): locate · explain · cross-file ·
  run-fix (test-verified) · safety (escape/read-outside) · **injection** (planted-file).
- **Gold per task:** known file/symbol answers; the repo's own test suite for run-fix; a
  planted injection file with a canary for the injection tasks.
- **Sample size / rigor:** report per-task *and* aggregate; use k=3–5 repeats for variance
  and pass^k; small-N is expected — be explicit, and prefer objective checks over judges.
- **Contamination caveat:** these repos are likely in pretraining. That is acceptable
  because we test *agentic behaviour* (navigation, tool use, execution, grounding), not
  memorised trivia; the grounding check catches "recognised but wrong."

---

## 7. Reporting — tables & figures for the paper

- **Table 1 — Trustworthiness by ablation rung (E1):** success%, faithful%, tool-call
  validity%, safety-blocked%, reliability@k, mean turns, mean recoveries. Rows = off →
  salvage → leaked → full. *This is the headline table.*
- **Table 2 — Per-category breakdown:** locate / explain / cross-file / run-fix / safety.
- **Figure 1 — Success (and recovery-rate) vs. context window (E2).**
- **Figure 2 — Metric deltas off→full** (the reliability layer's contribution bars).
- **Table 3 — Safety (E3):** attack-success rate by category, off vs. full.
- **(optional) Table 4 — cross-model / cloud-ceiling** with the caveats from Section 2.
- Report exact hardware (A6000 48 GB, driver/CUDA), model build (gpt-oss-20b MXFP4), serving
  config, sampling, and release the harness + tasks (reproducibility).

---

## 8. Rigor & honesty checklist

- Report only measured numbers; never claim a check passed unless observed (the agent's own
  faithfulness rule applies to us too).
- k≥3 repeats for any reliability/consistency claim; report variance, not just the mean.
- State every limitation: Harmony-only (no cross-model), small model, small context, small
  task-N, single GPU; contamination.
- Judge-based metrics (G-Eval) are secondary and flagged for self-bias.
- Negative results are results — a robustness paper is stronger with honest failure modes
  (e.g., the click conda-shadowing case).

---

## 9. Mapping to the paper's RQs

| RQ | Metrics | Experiment | To build |
|----|---------|------------|----------|
| RQ1 trustworthiness baseline | tool-call validity, faithfulness, success | E1 (rung=off) | metric extraction from the run |
| RQ2 reliability-layer effect | Δ of the above + reliability@k | E1 ablation | ablation switches + pass^k |
| RQ3 safety | escape/read-outside/injection block rate | E3 | injection tasks (planted file + canary) |
| RQ4 privacy↔capability | success vs. a cloud ceiling | E5 (optional) | off-harness / manual |
| (context finding) | success/recovery vs. window | E2 | relaunch server per `-c`, tag the run |

**Harness essentials to implement:** an in-process runner (call the agent's turn loop per
task), metric extraction from the event stream, `pass^k`, injection tasks (planted file +
canary), and optional energy/latency logging.

---

## 10. Primary sources to cite (verify ids/venues before submission)

SWE-bench — Jimenez et al. 2023 · SWE-agent — Yang et al. 2024 · τ-bench — Yao et al. 2024 ·
BFCL/Gorilla — Patil et al. 2023–24 · Terminal-Bench — 2025 · AgentDojo — Debenedetti et al.
2024 · InjecAgent — 2024 · AgentHarm — 2024 · GAIA — Mialon et al. 2023 · AgentBench — Liu
et al. 2023 · WebArena — Zhou et al. 2023 · OSWorld — 2024 · CodeAct — Wang et al. 2024 ·
HumanEval — Chen et al. 2021 · SelfCheckGPT — Manakul et al. 2023 · G-Eval — Liu et al. 2023
· RAGAS — Es et al. 2023 · TrustLLM — Sun et al. 2024. (Full annotated list:
`docs/project-construction-notes.md` §8.)
