# Evaluation & Trustworthiness — metrics for a local code-understanding (+ edit) agent

How do we know this agent is *good* and *trustworthy* — especially on codebases nobody has
read? This surveys the metric families and named frameworks, explains how each helps **our
system** (navigate-then-answer, soon edit), and lands on a concrete evaluation plan.

## 0. The core difficulty (and why it shapes everything)

Our agent is effectively a **RAG system for code**: it *retrieves* (`list_dir`/`glob`/`grep`/
`lsp`/`read`) and then *generates* an answer or an edit. Two consequences:
1. On an **unknown** repo we have **no answer key**, so reference-free and auto-gold methods
   matter most.
2. A polished-looking answer can still be **ungrounded** (invented from the model's prior
   knowledge). Notably, TrustLLM found LLMs are far more truthful **with external knowledge**
   than from memory alone — which is exactly the bet our grounded design makes, and exactly why
   we must *measure* grounding. ([TrustLLM](https://arxiv.org/abs/2401.05561))

## 1. Answer correctness / quality
- **Exact-match / F1** against a gold answer — only where gold exists (auto-generated "mechanical"
  questions: "where is X defined", "which files import Y").
- **LLM-as-judge / G-Eval** — a stronger model scores answers against a rubric (correctness,
  completeness) with chain-of-thought; flexible but degrades on long, multi-step contexts, so use
  it with a clear rubric and spot-check. ([G-Eval / judge caveats](https://www.datadoghq.com/blog/ai/llm-hallucination-detection/))
- **Task success** — did the agent actually answer the question / resolve the issue.

## 2. Groundedness / faithfulness — the RAG lens (most important for us)
Our navigate-then-answer *is* RAG, so the **RAGAS** metrics map directly and need **no gold
labels** (LLM-scored), separating *retrieval* failures from *generation* failures:
- **Faithfulness** — is every claim in the answer supported by the code it actually read? (our
  #1 guard against hallucination).
- **Answer relevance** — does the answer address the question?
- **Context precision** — were the files/lines it read actually relevant (little junk)?
- **Context recall** — did it read *enough* to support the answer?
Together they tell you whether a bad answer is a *retrieval* miss (grep/lsp) or a *generation*
miss (model). ([RAGAS metrics](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/),
[Confident AI — RAG metrics](https://www.confident-ai.com/blog/rag-evaluation-metrics-answer-relevancy-faithfulness-and-more))

**Our cheap mechanical version (no judge needed):** parse the answer's `file:line` citations,
re-read them, and verify the claim actually appears there; flag uncited claims. This is a
concrete faithfulness/citation-accuracy check we can run offline on any repo — the "groundedness
harness" from earlier.

## 3. Retrieval quality (the navigate step in isolation)
Score `glob`/`grep`/`lsp` against known-answer queries with **precision / recall / MRR / nDCG**
("did the right file:line appear, and how high"). Auto-gold is free here: ctags/AST/`lsp` give the
true definition, the import graph gives the true importers. This directly measures the funnel that
grounds every answer.

## 4. Hallucination detection (reference-free)
- **SelfCheckGPT** — sample the answer N times at temperature; measure consistency (NLI/BERTScore).
  High divergence ⇒ likely hallucination. Needs **no gold**, so it works on unknown repos.
  ([SelfCheckGPT](https://www.comet.com/site/blog/selfcheckgpt-for-llm-evaluation/),
  [awesome-hallucination-detection](https://github.com/EdinburghNLP/awesome-hallucination-detection))
- **FActScore / fact-level checks** — decompose the answer into atomic facts and verify each
  against the read code; fact-level is more sensitive than whole-answer scoring.
  ([FactSelfCheck](https://arxiv.org/pdf/2503.17229))
- **Self-consistency** — ask the same question paraphrased ×N; low agreement = unreliable.

## 5. Trustworthiness dimensions (TrustLLM framework)
TrustLLM defines eight dimensions; its benchmark covers six. Mapped to *our* code agent:

| Dimension | For this agent |
|---|---|
| **Truthfulness** | grounded, non-hallucinated answers (§2, §4) — our central axis |
| **Safety** | the `bash`/edit tools don't run/write destructive things (deny-lists, sandbox, opt-in) |
| **Robustness** | stable under paraphrase, noisy repos, malformed model output (our salvage/recovery layers) |
| **Privacy** | fully local — nothing leaves the box (a built-in strength; verify no egress) |
| **Fairness / Machine ethics** | low relevance for code Q&A, but: no biased refusals, honest "I don't know" |
| **Transparency / Accountability** | show the tool trace + citations so a human can audit *why* an answer was given |

TrustLLM's headline finding — external knowledge boosts truthfulness — validates the grounded
design and says: **the more we ground (LSP, citations), the more trustworthy.**
([TrustLLM paper](https://arxiv.org/abs/2401.05561), [repo](https://github.com/HowieHwong/TrustLLM))

## 6. Calibration & uncertainty (does it know when it doesn't know?)
- **Abstention / "I don't know"** rate — a trustworthy agent says so rather than inventing.
- **Expected Calibration Error (ECE)** — do stated/derived confidences match actual accuracy?
- Practical proxy: does it *ground before asserting*, and does it flag inferences vs verified facts
  (our instructions already push this).

## 7. Robustness & consistency
Paraphrase invariance, resilience to a repo it can't fully index, and graceful handling of its own
malformed output (we already recover from malformed tool-call headers, leaked calls, empty finals,
context overflow — each of those recovery rates is a robustness metric to track).

## 8. Code-specific metrics (especially once the edit tier lands)
- **HumanEval / MBPP — `pass@k`**: functional correctness of generated code (single-function). The
  origin of `pass@k` = "does any of k samples pass the unit tests." ([HumanEval](https://www.datacamp.com/tutorial/humaneval-benchmark-for-evaluating-llm-code-generation-capabilities))
- **SWE-bench — the north star for the edit tools**: real GitHub issues in real repos; the agent
  must *navigate the codebase, locate files, write a patch, and pass the hidden test suite*. This is
  exactly our read→`lsp`→`edit`→`bash` loop, scored by **resolved-rate / test-pass**.
  ([SWE-bench overview](https://runloop.ai/blog/understanding-llm-code-benchmarks-from-humaneval-to-swe-bench))
- **No-regression**: after an edit, the previously-passing tests still pass.

## 9. Operational metrics (cheap, always-on)
Turns, tool calls, **tokens** (we already track prompt/new/output), latency/tokens-per-sec, outcome
mix (completed / no-answer / max-turns / cancelled), and recovery/salvage counts. These are the
cost-and-reliability axes and pair with the quantization study (`quantization-research.md`) — e.g.
"does Q5_K_M keep the agent task score while improving tokens/sec?"

## 10. Frameworks & tools (what to actually use)
| Tool / framework | Gives us |
|---|---|
| **RAGAS** | faithfulness, answer relevance, context precision/recall (the RAG lens) |
| **TrustLLM** | the trustworthiness dimension taxonomy + benchmark datasets |
| **DeepEval / TruLens / Confident AI** | ready-made metric implementations + LLM-as-judge harnesses |
| **G-Eval** | rubric-based LLM-judge scoring |
| **SelfCheckGPT / FActScore** | reference-free hallucination detection |
| **SWE-bench / HumanEval / MBPP** | code correctness (edit tier) |
| *(ours)* mechanical-gold + citation-verification harness | offline, no-judge grounding + retrieval scores on any repo |

## 11. Concrete evaluation plan for this project
Build a small `eval/` harness (ties together the earlier "evaluation approaches" and the LSP/quant
work):

1. **Mechanical-gold generator** (no judge, no gold-writing): from static analysis / `lsp`, auto-make
   questions with exact answers ("where is X defined", "who imports Y") → score retrieval
   **precision/recall** and answer **exact-match**. Runs on *any* repo, offline.
2. **Citation-verification (faithfulness) checker**: parse `file:line` in answers, re-read, verify →
   a **groundedness rate**; flag uncited claims. Offline, no judge.
3. **Self-consistency**: N paraphrases, measure agreement → reliability, no gold.
4. **LLM-as-judge (G-Eval/RAGAS)** for the hard conceptual questions — run offline with a stronger
   judge (paste transcripts) or a second local model on the GPU box.
5. **Operational + trust dashboard**: outcome mix, tokens, latency, recovery/salvage rates,
   abstention rate, and — once editing lands — **SWE-bench-style test-pass**.
6. **Use it as the quant yardstick**: re-run across MXFP4 / Q5_K_M / Q6_K / Q8_0 to catch reasoning
   degradation perplexity would miss.

**Start with (1)+(2)** — objective retrieval scores + a hallucination guard, both offline and
judge-free, which is the highest-signal, lowest-cost foundation and directly measures the thing our
architecture is built to do: **answer from the code, not from memory.**

## 5 W's — the decision in one frame

**WHAT** — Whether the agent's answers (and soon edits) are **correct, grounded (not
hallucinated), and trustworthy** — plus how well retrieval finds the right code and how
robust/efficient the loop is. Grounding is the headline: *answer from the code it read, not from
memory.*

**WHY** — On an **unknown repo** a slick answer can be invented, and once the edit tier lands a
wrong patch is costly. Trustworthiness = truthfulness + safety + robustness + privacy; TrustLLM
shows external knowledge is what makes LLMs truthful — so grounding must be **measured**, not assumed.

**WHO** — Us, to tune the agent **and as the yardstick for the quantization sweep**; and any user
who relies on the agent's answers/edits enough to act on them.

**WHERE** — A local **`eval/` harness**. The judge-free parts (mechanical gold, citation/
groundedness, self-consistency, operational stats) run **offline on the build machine**; the
LLM-judge (RAGAS/G-Eval) and SWE-bench-style parts run where a model is (GPU box, or an offline judge).

**WHEN** — Per meaningful change (**regression guard**), as the **quant yardstick** (catches
reasoning loss perplexity misses), and **before shipping the edit tier** (SWE-bench-style test-pass).

**HOW (next)** — Start with **(1) mechanical-gold** retrieval/answer scoring and **(2)
citation-verification groundedness** — both offline, judge-free, on any repo — then layer
self-consistency, an LLM judge, and the trust dashboard.

## Sources
- [TrustLLM (arXiv 2401.05561)](https://arxiv.org/abs/2401.05561) · [TrustLLM repo](https://github.com/HowieHwong/TrustLLM)
- [RAGAS metrics](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/) · [Confident AI — RAG metrics](https://www.confident-ai.com/blog/rag-evaluation-metrics-answer-relevancy-faithfulness-and-more)
- [SelfCheckGPT (Comet)](https://www.comet.com/site/blog/selfcheckgpt-for-llm-evaluation/) · [awesome-hallucination-detection](https://github.com/EdinburghNLP/awesome-hallucination-detection) · [FactSelfCheck (arXiv)](https://arxiv.org/pdf/2503.17229) · [LLM-as-judge / G-Eval (Datadog)](https://www.datadoghq.com/blog/ai/llm-hallucination-detection/)
- [HumanEval (DataCamp)](https://www.datacamp.com/tutorial/humaneval-benchmark-for-evaluating-llm-code-generation-capabilities) · [HumanEval→SWE-bench (Runloop)](https://runloop.ai/blog/understanding-llm-code-benchmarks-from-humaneval-to-swe-bench)
