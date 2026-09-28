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
> edge and cyber-physical environments, made **trustworthy and reliable** by a
> **reliability layer** (tolerant tool-call recovery) and a **fail-closed permission
> model**, and characterized with a **trustworthiness evaluation** of its agentic behaviour.

Why it fits SS1: the system is on-device/edge AI (no cloud, single GPU, fully local),
it targets secure/air-gapped CPS deployments, and the contribution is measured
**trustworthiness** (tool-call validity, faithfulness/grounding, task success, safety of
the tool/permission boundary) rather than raw model accuracy.

---

## 3. The gap / motivation (what to argue in the intro)

- Cloud coding assistants (Copilot, Claude Code, Cursor) are powerful but **send source code
  off-device** — a non-starter for air-gapped CPS, defense, industrial OT, healthcare, and
  IP-sensitive edge deployments.
- A small **local** open model (gpt-oss-20b) is far less polished than a frontier cloud
  model at **structured tool-calling** — it emits malformed function-call syntax and
  reasons without committing to an action — the exact behaviours an agent depends on.
- Prior work measures such models mostly by **perplexity / accuracy on QA**, not by
  **agentic trustworthiness** (tool-call validity, faithfulness/grounding, multi-step task
  success, and safety of the tool boundary). That gap is the paper's opening.

---

## 4. Contributions (claim these explicitly)

- **C1 — System.** An offline, privacy-preserving agent architecture that runs a 20B MoE
  model (gpt-oss-20b) on a single ≤16 GB GPU with **no cloud and no third-party services**:
  client-side Harmony rendering over llama.cpp's raw endpoint, a hand-rolled single-agent
  ReAct tool-loop, a permission-gated sandbox, and **retrieval-free "navigate-don't-index"**
  code context (glob→grep→read) — no vector DB / embedding model / index required.
- **C2 — Method (headline novelty).** A **reliability layer** that restores agentic
  trustworthiness on a small local model: (i) tolerant Harmony parsing with
  **malformed-tool-call-header salvage**, (ii) **leaked-tool-call recovery** (dispatching
  calls the model emits as prose/JSON in the wrong channel), and (iii) a **tool-less
  synthesis fallback** that forces an answer when the model spins in analysis-only turns —
  paired with a **fail-closed permission model + sandbox** that treats tool output as
  untrusted (the trust boundary for an air-gapped deployment).
- **C3 — Evaluation.** An **offline trustworthiness evaluation** of the agent on **agentic**
  metrics — tool-call validity, **faithfulness/grounding** (answers cite code actually read;
  hallucination rate), task success, recovery/turn efficiency — with **ablations** isolating
  each reliability mechanism, and a safety check of the permission boundary
  (prompt-injection / unauthorized-write resistance).

---

## 5. Candidate titles

1. *A Trustworthy Offline LLM Code Agent for Air-Gapped Edge and Cyber-Physical Systems*
2. *Making a Small Local Code Agent Trustworthy: A Reliability Layer and Its Evaluation*
3. *Navigate, Don't Index: A Retrieval-Free, Trustworthy Code Agent for the Edge*
4. *Privacy-Preserving, Trustworthy Agentic Code Understanding at the Edge with gpt-oss-20b*

Recommended: **#1** (system + venue keywords) or **#2** (leads with the novelty).

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
| Tool funnel (navigate-don't-index) | `agent/tools/` (list_dir/glob/grep/read) | retrieval-free context (C1) |
| Permission engine + sandbox | `agent/permissions.py`, `agent/sandbox.py` | security model (secondary) |
| Offline tokenizer | `agent/harmony_codec.py` (vendored o200k) | fully-offline claim |
| Eval harness + benchmark | *(to be rebuilt against small real repos)* | C3 |

Include an **architecture figure** (reuse `docs/architecture/architecture*.svg`) and a
**deployment figure** (edge box, air-gapped, no cloud arrow).

---

## 8. Novelty deep-dive (the "method" section)

- **8.1 Agentic failure modes of a small local model.** Characterize *how* untrustworthy
  behaviour manifests: malformed tool-call headers (duplicated recipients), tool calls leaked
  into the reasoning channel, analysis-only "empty final" spins, and ungrounded/hallucinated
  claims. Quantify the base rate of each on the benchmark (this framing is itself a contribution).
- **8.2 The reliability layer.** Describe the three mechanisms (salvage / leaked-call
  recovery / tool-less synthesis) with a small algorithm box each, and where they sit in the
  loop. Key claim: they recover trustworthiness *without* changing the model or needing a larger one.
- **8.3 The trust boundary.** The fail-closed permission model + path sandbox, and treating
  tool output as untrusted input — the safety guarantee that makes an autonomous local agent
  acceptable for air-gapped/CPS use.
- **8.4 Retrieval-free context.** Argue why "navigate-don't-index" (live glob→grep→read)
  suits the edge — no embedding model, no vector store, no index to build or keep fresh;
  works on unseen repos with a tiny footprint.

---

## 9. Experiments & evaluation plan

**Research questions**
- **RQ1 (trustworthiness baseline):** How untrustworthy is the raw local model as an agent —
  tool-call validity, faithfulness/grounding (hallucination rate), and task success?
- **RQ2 (novelty):** How much does the reliability layer improve each of those? (ablations:
  {off, salvage-only, +leaked-recovery, +synthesis (full)}).
- **RQ3 (safety):** Does the permission model + sandbox hold against adversarial tool output
  and unauthorized-write / path-escape attempts (prompt-injection resistance)?
- **RQ4 (privacy/capability):** How does the offline agent compare to a cloud baseline on
  task success, and is the gap acceptable given the privacy guarantee? *(Upper-bound only —
  the point is "good enough, fully local," not beating the cloud.)*

**Setup**
- Hardware: the SRM-AP GPU box (record exact GPU, VRAM, driver). Single fixed serving
  config (native MXFP4, the box's KV/context) — **quantization is out of scope**; we report
  the one configuration used, not a sweep.
- Sampling/reasoning held fixed (report the values); vary only the reliability-layer ablation.

**Metrics** (mostly emitted by the loop already)
- Tool-call validity rate (strict-parse fails + salvage + leaked recoveries).
- **Faithfulness / grounding**: fraction of claims traceable to code the agent read;
  hallucination rate (SelfCheckGPT-style self-consistency; G-Eval rubric as a secondary judge).
- Task success rate (objective checks / test suites); turns-to-completion; recovery rate.
- Safety: unauthorized-write / path-escape attempts blocked (should be 100%).
- Latency (tokens/s, time-to-first-token) as a secondary efficiency note.

**Benchmark** *(to be assembled — the earlier synthetic fixture + harness were removed)*
- Assemble **2–4 small real OSS repos** (different languages) with objective checks
  (known file/symbol answers, existing test suites, and one or two seeded-bug fix tasks
  verified by the repo's own tests).
- Build a headless eval driver (run the agent per task via `loop.run_turn`, score
  objectively) that captures tool-call validity, task success, turns, latency, VRAM, energy.
- Report per-category (locate/explain/edit/run-fix) and aggregate.

**Baselines**
- Reliability-layer-**OFF** — the key contrast for RQ2.
- (Optional) a RAG/embedding retrieval baseline for the navigate-don't-index claim.
- (Optional, clearly framed) one cloud LLM as a capability ceiling for RQ4.

**Expected results / hypotheses (fill with real numbers)**
- H1: the raw local model has a measurable base rate of invalid tool calls and ungrounded
  claims (RQ1).
- H2: the reliability layer improves tool-call validity, faithfulness, and task success
  (headline table + ablation).
- H3: the permission model + sandbox block 100% of unauthorized-write / path-escape /
  injected-instruction attempts (RQ3).

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

- **Trustworthiness / evaluation (primary):** TrustLLM, SelfCheckGPT, G-Eval, RAGAS;
  faithfulness/grounding and hallucination measurement (see
  `docs/research/evaluation-and-trustworthiness.md`).
- Agentic coding & tool use: ReAct, SWE-bench / SWE-agent / CodeAct, tool-call reliability
  studies (Berkeley Function-Calling Leaderboard).
- Structured decoding / function-calling robustness.
- Safety of tool-using agents: prompt-injection, sandboxing, permission models.
- Privacy-preserving / on-prem code AI; retrieval-augmented vs retrieval-free code context.
- Edge / on-device LLM inference (llama.cpp, MoE serving) — background, not the focus.

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
| D1–D2 | Freeze scope; assemble the benchmark repos + build the eval driver with energy logging |
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
model, serving-strategy findings.

**Build/measure for the paper:**
- [ ] Rebuild the eval harness (headless driver + benchmark repos) with energy/latency/VRAM logging (per-task J, tokens/J, peak VRAM).
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
