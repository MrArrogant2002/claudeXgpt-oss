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

**How the field evaluates agentic systems (positioning).** Frontier labs report a fairly
consistent battery: for *coding agents*, a **test-verified resolve rate** on real issues
(SWE-bench / SWE-bench Verified) and terminal/agent-interface benchmarks (SWE-agent,
Terminal-Bench); for *tool use*, function-call correctness (BFCL) and multi-turn
tool-agent-user tasks scored by both success and **reliability across repeated trials**
(τ-bench's `pass^k`); for *general agency*, web/OS/embodied suites (GAIA, WebArena, OSWorld,
Mind2Web); and for *safety*, prompt-injection and harmful-behaviour suites (AgentDojo,
InjecAgent, AgentHarm). gpt-oss's own card reports SWE-bench Verified, τ-bench, Codeforces,
and standard academic tests; Anthropic's Claude system cards report SWE-bench Verified,
Terminal-Bench, τ-bench, and agentic-safety evaluations. Our plan **borrows these metrics**
(resolve rate, `pass@k`/`pass^k`, tool-call validity, injection-resistance) but applies them
to a small local repo suite rather than reproducing the full public harnesses — appropriate
for a constrained, offline single-GPU system. Full provenance in Section 10.

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

## 10. Sources & provenance

Prefer the **primary** source (the paper that introduced the metric/benchmark); items
marked *(secondary)* are leaderboards, blog posts, or model cards — cite them only for "how
a system is evaluated," not for the method itself. **arXiv ids below are best-effort — verify
every id, and record the official venue DOI + a stable URL (`https://arxiv.org/abs/<id>` is a
stable URL; add the published DOI where one exists) before submitting** (per `AGENTS.md`).
The metric each source grounds is in the last column.

**Coding-agent benchmarks**

| Benchmark | Primary source (authors, year, venue) | Ref (verify) | Grounds |
|---|---|---|---|
| SWE-bench | Jimenez, Yang, Wettig, Yao, Pei, Press, Narasimhan; 2023 (ICLR'24) | arXiv:2310.06770 | test-verified resolve rate |
| SWE-bench Verified *(secondary)* | OpenAI, 2024 (blog) | openai.com/index/introducing-swe-bench-verified | resolve rate (validated subset) |
| SWE-agent | Yang, Jimenez, Wettig, Lieret, Yao, Narasimhan, Press; 2024 (NeurIPS'24) | arXiv:2405.15793 | agent-computer interface / success |
| Terminal-Bench *(secondary)* | Terminal-Bench Team, 2025 (leaderboard) | tbench.ai | agentic shell-task success |
| CodeAct | Wang, Chen, Yuan, Zhang, Li, Peng, Ji; 2024 (ICML'24) | arXiv:2402.01030 | executable-code actions |
| HumanEval | Chen et al.; 2021 | arXiv:2107.03374 | pass@k (code synthesis) |
| MBPP | Austin et al.; 2021 | arXiv:2108.07732 | code synthesis |
| LiveCodeBench | Jain et al.; 2024 *(confirm id)* | arXiv:2403.07974 | contamination-free coding |
| BigCodeBench | Zhuo et al.; 2024 *(confirm id)* | arXiv:2406.15877 | practical library-use coding |
| Aider polyglot *(secondary)* | Aider, leaderboard | aider.chat/docs/leaderboards | multi-language edit + edit-format |

**Tool use / function calling**

| Benchmark | Primary source | Ref (verify) | Grounds |
|---|---|---|---|
| Gorilla (APIs) | Patil, Zhang, Wang, Gonzalez; 2023 | arXiv:2305.15334 | API/tool-call correctness |
| BFCL *(secondary)* | Berkeley/Gorilla team, 2024 (leaderboard) | gorilla.cs.berkeley.edu/leaderboard | tool-call validity/correctness, irrelevance |
| τ-bench | Yao, Shinn, Razavi, Narasimhan (Sierra); 2024 *(confirm id)* | arXiv:2406.12045 | task success + **pass^k** reliability |
| τ²-bench | Sierra; 2025 *(confirm id)* | (arXiv 2025 — confirm) | dual-control tool-agent tasks |
| ToolLLM | Qin et al.; 2023 (ICLR'24) | arXiv:2307.16789 | tool use over many APIs |
| API-Bank | Li et al.; 2023 *(confirm id)* | arXiv:2304.08244 | tool-augmented dialogue |

**General agency**

| Benchmark | Primary source | Ref (verify) | Grounds |
|---|---|---|---|
| GAIA | Mialon, Fourrier, Swift, Wolf, LeCun, Scialom; 2023 | arXiv:2311.12983 | general assistant success |
| AgentBench | Liu et al.; 2023 (ICLR'24) | arXiv:2308.03688 | multi-environment agency |
| WebArena | Zhou et al.; 2023 (ICLR'24) | arXiv:2307.13854 | web-navigation success |
| Mind2Web | Deng et al.; 2023 (NeurIPS'23) | arXiv:2306.06070 | generalist web agent |
| OSWorld | Xie et al.; 2024 *(confirm id)* | arXiv:2404.07972 | computer-use success |
| MLE-bench | Chan et al. (OpenAI); 2024 *(confirm id)* | arXiv:2410.07095 | ML-engineering agency |
| SWE-Lancer *(secondary)* | OpenAI; 2025 *(confirm id)* | arXiv:2502.12115 | economic ($) task value |

**Safety / robustness**

| Benchmark | Primary source | Ref (verify) | Grounds |
|---|---|---|---|
| AgentDojo | Debenedetti et al. (incl. Tramèr); 2024 (NeurIPS'24 D&B) *(confirm id)* | arXiv:2406.13352 | prompt-injection resistance |
| InjecAgent | Zhan et al.; 2024 *(confirm id)* | arXiv:2403.02691 | indirect prompt injection |
| AgentHarm | Andriushchenko et al.; 2024 *(confirm id)* | arXiv:2410.09024 | harmful-request refusal |

**Faithfulness / trustworthiness evaluation**

| Method | Primary source | Ref (verify) | Grounds |
|---|---|---|---|
| SelfCheckGPT | Manakul, Liusie, Gales; 2023 (EMNLP'23) | arXiv:2303.08896 | hallucination via self-consistency |
| G-Eval | Liu, Iter, Xu, Wang, Xu, Zhu; 2023 (EMNLP'23) | arXiv:2303.16634 | LLM-as-judge scoring |
| RAGAS | Es, James, Espinosa-Anke, Schockaert; 2023 (EACL'24 demo) | arXiv:2309.15217 | faithfulness/answer metrics |
| TrustLLM | Sun et al.; 2024 (ICML'24) *(confirm id)* | arXiv:2401.05561 | trustworthiness taxonomy |

**Metric origins & model cards** *(cite for definitions / "how they're evaluated")*

| Item | Source | Ref (verify) |
|---|---|---|
| `pass@k` estimator | Chen et al.; 2021 (HumanEval) | arXiv:2107.03374 |
| `pass^k` (reliability@k) | Yao et al.; 2024 (τ-bench) | arXiv:2406.12045 *(confirm)* |
| gpt-oss evaluation | OpenAI gpt-oss model card; 2025 *(secondary)* | openai.com / HF `openai/gpt-oss-20b` |
| Claude evaluation | Anthropic model/system cards; 2024–25 *(secondary)* | anthropic.com |
| MMLU / MMLU-Pro / GPQA *(report-only)* | Hendrycks 2020 / Wang 2024 / Rein 2023 | arXiv:2009.03300 / 2406.01574 / 2311.12022 |

Cross-reference: the annotated reading list in `docs/project-construction-notes.md` §8 has
the "why it matters" notes for the agent-architecture and quantization literature.
