# proj-conf.md — Converting this project into an ICDEC-2026 paper

> **Purpose of this file.** A *planning* document that turns the local code agent into a
> conference paper for **ICDEC-2026, Special Session SS1**. It is scaffolding — research
> questions, contributions, experiment matrix, section outline, timeline. **It is not the
> paper.** You write the paper prose yourself (see *AI-content compliance* below).

---

## 1. Target venue (verify on the official site before submitting)

| Field | Value |
|-------|-------|
| Conference | ICDEC-2026 — 5th Intl. Conf. on Data, Electronics and Computing |
| Special session | **SS1 — Intelligent Edge Devices: Real-Time AIoT, Edge Computing & Cyber-Physical Applications** |
| Organizers | SVNIT Surat & COMSYS Educational Society, Kolkata; SS1 organizer: Dr. Raushan Kumar Singh (CSE, **SRM University-AP**) |
| Mode | Hybrid (online + on-campus) |
| Format | **Springer LNNS, 8–12 pages** |
| Submission | Microsoft CMT — `cmt3.research.microsoft.com/ICDEC2026`, **Track: Special Session SS1** |
| **Submission deadline** | **Oct 01, 2026** |
| Acceptance | Nov 01, 2026 · Registration Nov 10, 2026 · Sessions Nov 26–28, 2026 |
| Hard rules | Original & unpublished only · **AI-generated content is desk-rejected** · must follow Springer plagiarism + AI-content policy · accepted papers (if approved) go to Springer LNNS |

Favorable context: the SS1 organizer is at SRM-AP (the institution where the GPU box runs),
i.e. an in-house special session.

---

## 2. Thesis (one sentence)

> A **fully offline, single-GPU LLM code-understanding-and-editing agent** for air-gapped
> edge and cyber-physical environments, made reliable on aggressively **quantized** models
> by a **quantization-robustness layer**, and characterized for **accuracy, energy, and
> reliability** on commodity edge hardware.

Why it fits SS1: the system is on-device/edge AI (no cloud, ≤16 GB GPU, MXFP4 + 8-bit KV),
it targets secure/air-gapped CPS deployments, and we measure the low-power/energy trade-offs
the session cares about.

---

## 3. The gap / motivation (what to argue in the intro)

- Cloud coding assistants (Copilot, Claude Code, Cursor) are powerful but **send source code
  off-device** — a non-starter for air-gapped CPS, defense, industrial OT, healthcare, and
  IP-sensitive edge deployments.
- Running a capable agent **locally on a constrained edge GPU** forces aggressive
  quantization, which **degrades structured tool-calling** (malformed function-call syntax,
  reasoning that never commits to an action) — the exact behaviors an agent depends on.
- Prior work measures quantization mostly by **perplexity / accuracy on QA**, not by
  **agentic reliability** (tool-call validity, multi-step task success). That gap is the
  paper's opening.

---

## 4. Contributions (claim these explicitly)

- **C1 — System.** An offline, privacy-preserving agent architecture that runs a 20B MoE
  model (gpt-oss-20b) on a single ≤16 GB GPU with **no cloud and no third-party services**:
  client-side Harmony rendering over llama.cpp's raw endpoint, a hand-rolled single-agent
  ReAct tool-loop, a permission-gated sandbox, and **retrieval-free "navigate-don't-index"**
  code context (glob→grep→read→LSP) plus a **staleness-aware on-device project memory**
  (`local_mind.md`) — no vector DB / embedding model required.
- **C2 — Method (headline novelty).** A **quantization-robustness layer** that restores
  agentic reliability lost to quantization on small local models: (i) tolerant Harmony
  parsing with **malformed-tool-call-header salvage**, (ii) **leaked-tool-call recovery**
  (dispatching calls the model emits as prose/JSON in the wrong channel), and (iii) a
  **tool-less synthesis fallback** that forces an answer when the model spins in
  analysis-only turns. *(Optional extension: energy-adaptive reasoning-effort control.)*
- **C3 — Evaluation.** An **offline eval harness + multi-language benchmark** that measures
  quantization/serving configurations on **agentic** metrics — tool-call validity, task
  success, turns, latency, VRAM, and energy — quantifying accuracy/energy/reliability
  trade-offs for edge deployment, with **ablations** isolating each robustness mechanism.

*(Secondary, if space: a security subsection — fail-closed permission model + treating tool
outputs as untrusted for prompt-injection resistance.)*

---

## 5. Candidate titles

1. *An Offline, Quantization-Robust LLM Code Agent for Air-Gapped Edge and Cyber-Physical Systems*
2. *When Quantization Breaks Tool-Calling: A Reliability Layer for On-Device Code Agents*
3. *Navigate, Don't Index: A Retrieval-Free, Energy-Aware Code Agent for Constrained Edge GPUs*
4. *Privacy-Preserving Agentic Code Understanding at the Edge with gpt-oss-20b*

Recommended: **#1** (system + novelty + venue keywords) or **#2** (leads with the novelty).

---

## 6. Abstract — content skeleton (write the prose yourself)

Structure the ~180-word abstract so each part conveys:
1. **Context/problem:** cloud code assistants leak source; edge/CPS needs on-device.
2. **Challenge:** fitting a capable agent on a ≤16 GB GPU needs quantization, which harms
   tool-calling reliability.
3. **What you built (C1):** the offline single-GPU agent, one clause on the architecture.
4. **Your novelty (C2):** the quantization-robustness layer.
5. **Evaluation (C3):** the harness + benchmark; name the metrics.
6. **Headline result:** the robustness layer raises tool-call validity / task success by
   *X* points at *Y*× lower energy than the cloud/full-precision baseline (fill from data).
7. **Takeaway:** capable, private, reliable agentic coding is feasible at the edge.

> Do **not** paste generated prose. Draft it in your own words from the bullets above.

---

## 7. System description (factual — cite the repo)

Map each component to a figure/paragraph. All of this already exists:

| Component | Where | Paper role |
|-----------|-------|-----------|
| Client-side Harmony render/parse | `agent/harmony_codec.py` | protocol layer; why raw `/completion`, not a chat template |
| Single-agent ReAct loop + recovery | `agent/loop.py` | orchestration; the robustness layer (C2) |
| Inference client (sampling, KV) | `agent/inference.py` | serving config knobs measured in C3 |
| Tool funnel (navigate-don't-index) | `agent/tools/` (list_dir/glob/grep/read/lsp) | retrieval-free context (C1) |
| Permission engine + sandbox | `agent/permissions.py`, `agent/sandbox.py` | security model (secondary) |
| On-device project memory | `agent/project_mind.py` (`local_mind.md`) | staleness-aware memory (C1) |
| Offline tokenizer | `agent/harmony_codec.py` (vendored o200k) | fully-offline claim |
| Eval harness + benchmark | `tests/eval/`, `demo-project/` | C3 |

Include an **architecture figure** (reuse `docs/architecture/architecture*.svg`) and a
**deployment figure** (edge box, air-gapped, no cloud arrow).

---

## 8. Novelty deep-dive (the "method" section)

- **8.1 Quantization → agentic failure modes.** Characterize *how* MXFP4/K-quant + KV-cache
  quantization manifests in an agent: malformed tool-call headers (duplicated recipients),
  tool calls leaked into the reasoning channel, and analysis-only "empty final" spins.
  Quantify the base rate of each on the benchmark (this framing is itself a contribution).
- **8.2 The robustness layer.** Describe the three mechanisms (salvage / leaked-call
  recovery / tool-less synthesis) with a small algorithm box each, and where they sit in the
  loop. Key claim: they recover reliability *without* changing the model or needing a larger one.
- **8.3 Retrieval-free context + on-device memory.** Argue why "navigate-don't-index" +
  a cached, staleness-triggered project map suits the edge (no embedding model, no vector
  store, works on unseen repos, tiny footprint).
- **8.4 (Optional) Energy-adaptive reasoning.** Propose choosing reasoning-effort per task
  from a cheap complexity signal to cut Joules/task; report the energy/accuracy curve.

---

## 9. Experiments & evaluation plan

**Research questions**
- **RQ1 (reliability):** How much does quantization degrade tool-call validity and task
  success vs a full-precision reference?
- **RQ2 (novelty):** How much of that loss does the robustness layer recover? (ablations)
- **RQ3 (efficiency):** What are the latency / VRAM / **energy-per-task** trade-offs across
  quantization + KV-cache + context configs on the edge GPU?
- **RQ4 (privacy/capability):** How does the offline agent compare to a cloud baseline on
  task success, and is the gap acceptable given the privacy guarantee? *(Upper-bound only —
  the point is "good enough, fully local," not beating the cloud.)*

**Setup**
- Hardware: the SRM-AP GPU box (record exact GPU, VRAM, driver); note ≤16 GB target.
- Model/serving configs to sweep: MXFP4 (native) · GGUF `Q4_K_M/Q5_K_M/Q6_K/Q8_0` ·
  KV-cache `f16/q8_0/q4_0` · context `32k/64k` · sampling `temp 0.6 vs 1.0, top_p 1.0` ·
  reasoning `low/medium/high`.
- Ablations (RQ2): robustness layer {off, salvage-only, +leaked-recovery, +synthesis (full)}.

**Metrics** (the harness already emits most)
- Tool-call validity rate (strict-parse fails + salvage + leaked recoveries).
- Task success rate (objective checks / test suites).
- Turns-to-completion, recovery/empty-final rate.
- Latency (tokens/s at 2k & 32k, time-to-first-token), peak VRAM (`nvidia-smi`).
- **Energy per task** (J) — `nvidia-smi --query-gpu=power.draw` integrated over wall-clock,
  or a wall-plug meter; report J/task and tokens/J.

**Benchmark**
- `demo-project/` (Nimbus) — multi-language, with locate/explain/cross-lang/run-fix tasks
  and a seeded bug with a checkable fix; run via `tests/eval/run_eval.py`.
- Add **2–3 small real OSS repos** (different languages) for external validity; write
  objective checks (known file/symbol answers, existing test suites).
- Report per-category (locate/explain/edit/run-fix) and aggregate.

**Baselines**
- Full-precision (or highest-bit that fits) reference — upper bound for RQ1.
- Robustness-layer-OFF — the key contrast for RQ2.
- (Optional) a RAG/embedding retrieval baseline for the navigate-don't-index claim.
- (Optional, clearly framed) one cloud LLM as a capability ceiling for RQ4.

**Expected results / hypotheses (fill with real numbers)**
- H1: quantization drops tool-call validity by a measurable margin vs full precision.
- H2: the robustness layer recovers most of that drop (headline table + ablation).
- H3: MXFP4 + q8_0 KV is the accuracy/energy sweet spot on ≤16 GB (supports the serving
  strategy already documented in the repo).

---

## 10. Mapping to SS1 topics (put a version of this table in the paper)

| SS1 topic | How the paper addresses it |
|-----------|----------------------------|
| TinyML & On-Device / Edge AI | 20B MoE agent on a single ≤16 GB edge GPU, fully offline |
| Low-Power, Energy-Efficient Computing | energy-per-task measurements; (optional) energy-adaptive reasoning |
| Secure & Trusted Embedded Systems | air-gapped operation; fail-closed permission + sandbox; untrusted tool output |
| Edge Analytics & AI-Driven … | retrieval-free code analysis + on-device project memory |
| Cyber-Physical Systems (CPS) | use case: diagnosing/fixing code on air-gapped OT/embedded repos |

---

## 11. Related work to read & cite (build the bibliography)

- Edge / on-device LLM inference (llama.cpp, MoE serving, quantized deployment).
- Quantization: GGUF k-quants, MXFP4/NVFP4, KV-cache quantization; QAT vs PTQ.
- Agentic coding & tool use: ReAct, SWE-bench / HumanEval, tool-call reliability studies.
- Structured decoding / function-calling robustness.
- Privacy-preserving / on-prem code AI; retrieval-augmented vs retrieval-free code context.
- Trustworthiness/eval: SelfCheckGPT, G-Eval, TrustLLM (see
  `docs/research/evaluation-and-trustworthiness.md`).

---

## 12. Paper structure (8–12 pp LNNS) with page budget

1. Introduction & motivation — 1–1.5 pp
2. Related work — 1–1.5 pp
3. System architecture (C1) — 1.5–2 pp (+ architecture figure)
4. Method: quantization-robustness layer (C2) — 1.5–2 pp (algorithm boxes)
5. Experimental setup — 1 pp
6. Results & ablations (C3) — 2–2.5 pp (tables + energy/accuracy plots)
7. Discussion, limitations, threats to validity — 0.5–1 pp
8. Conclusion & future work — 0.5 pp
- References — as needed.

---

## 13. Timeline (aggressive — ~10 days to Oct 01)

> This is tight for 8–12 pages **plus** experiments. Prioritize the minimum-viable paper:
> **RQ1 + RQ2 (robustness ablation) on the Nimbus benchmark** are the must-haves; energy
> (RQ3) and extra repos are stretch. If it cannot be done honestly by Oct 01, **email the
> SS organizer about an extension** or target the next deadline rather than rushing a weak paper.

| Days | Focus |
|------|-------|
| D1–D2 | Freeze scope; expand `tests/eval` tasks; wire energy logging into the harness |
| D3–D5 | Run the config sweep + ablations on the box; collect all metrics/reports |
| D4–D7 | **You draft** intro/related/system/method in your own words (in parallel) |
| D6–D8 | Make tables/plots from `reports/*.json`; write results & discussion |
| D8–D9 | Full read-through; LNNS formatting; plagiarism + AI-authenticity self-check |
| D10 | Final proof; submit via CMT to Track SS1 |

Post-submission: acceptance Nov 01 → register by Nov 10 → camera-ready → present Nov 26–28.

---

## 14. AI-content & integrity compliance (read this)

- **The paper prose must be authored by you.** ICDEC desk-rejects AI-generated content. Use
  this file and the tools to *plan, run experiments, and organize*, but write the sentences
  yourself. Do not paste model-generated paragraphs.
- Run an **AI-authenticity + plagiarism self-check** before submitting (the
  `research-paper-writer` skill in this session includes one; turn it on when drafting).
- **Reproducibility & honesty:** report the exact hardware, model build, and config; release
  the eval harness + fixtures; never report a number you did not measure. If a result is
  negative, report it — a robustness paper is stronger with honest failure modes.
- Original & unpublished: confirm nothing here overlaps a prior submission.

---

## 15. Gap list — have vs. build for the paper

**Already have:** offline agent, client-side Harmony + robustness layer (C2), permission
model, `local_mind.md`, eval harness + Nimbus benchmark, serving-strategy findings.

**Build/measure for the paper:**
- [ ] Energy/latency/VRAM logging in `tests/eval` (per-task J, tokens/J, peak VRAM).
- [ ] Robustness-layer **ablation switches** (config flags to disable salvage / leaked-recovery
      / synthesis) so RQ2 is a clean on/off study.
- [ ] Config-sweep driver (loop over model/quant/KV/temp, relaunch llama-server, tag runs).
- [ ] 2–3 extra small real repos + objective checks for external validity.
- [ ] Full-precision (or highest-fitting) reference run for RQ1.
- [ ] Figures: architecture, deployment (air-gapped), results/ablation plots.

---

## 16. Immediate next actions

1. Confirm the **spine** (recommend Framing A + robustness-layer headline + energy section).
2. I can add the **ablation switches** and **energy logging** to the eval harness (small,
   verifiable code changes) so you can start the sweep on the box.
3. You start drafting the intro + related work in your own words.
4. When drafting, invoke the `research-paper-writer` skill for LNNS formatting + the
   authenticity self-check.
