# Quantization Research — gpt-oss-20b, and how to find the best quant without quality loss

Goal: understand the options for compressing / running **gpt-oss-20b** efficiently, and a
principled plan to pick the quantization that minimizes quality loss for our local agent.

> **First, an important clarification the task conflates.** *Quantization* and *LoRA/QLoRA*
> are different things:
> - **Quantization** = making the *weights* use fewer bits (16→8→4…) so the model is smaller
>   and faster. This is what you deploy for inference. (GGUF k-quants, MXFP4, GPTQ, AWQ,
>   bitsandbytes.)
> - **LoRA** = *fine-tuning* method: freeze the model, train small low-rank adapters. It does
>   **not** shrink the base model.
> - **QLoRA** = LoRA *on top of* a 4-bit quantized frozen base — a way to *fine-tune* cheaply,
>   not a deployment quantization.
>
> For "run gpt-oss locally with the best quant," you want the **quantization** family. LoRA/
> QLoRA matter only if you later want to **adapt** gpt-oss to your data (§4).

## 1. gpt-oss-20b: the crucial fact — it's already 4-bit (MXFP4)

gpt-oss-20b is a **Mixture-of-Experts** model (~21B total parameters, ~3.6B active per token)
that OpenAI **trained and released natively in MXFP4** — a block-scaled 4-bit floating-point
format (OCP "microscaling": 4-bit elements sharing a per-block scale) applied to the MoE expert
weights. Because MXFP4 is the model's *native* precision (not a post-hoc squeeze), it packs to
**~12–13 GB** and runs in **~16 GB** of memory with **minimal quality loss** — the reference
precision, not a lossy approximation. ([Unsloth run guide](https://unsloth.ai/docs/models/gpt-oss-how-to-run-and-fine-tune), [IntuitionLabs](https://intuitionlabs.ai/articles/hardware-requirements-gpt-oss-20b), [llama.cpp gpt-oss guide](https://github.com/ggml-org/llama.cpp/discussions/15396))

**Implication for us:** "quantizing gpt-oss further" is mostly the *wrong* frame. The model is
already at 4-bit. The real question is: **keep native MXFP4, or *upcast* selected tensors to
higher precision (Q6/Q8) for a little more quality at the cost of memory?** Going *below* MXFP4
generally just degrades a reasoning model. So our experiment (§5) compares MXFP4 against
higher-bit variants, not lower.

## 2. Inference quantization methods (post-training / PTQ)

| Method | Bits | How it works | Tooling / runtime | Best for |
|---|---|---|---|---|
| **GGUF k-quants** (`Q4_K_M`, `Q5_K_M`, `Q6_K`, `Q8_0`, …) | 2–8 | Block quantization with mixed precision per tensor; optional **imatrix** calibration | **llama.cpp** (what we use) | CPU/GPU local inference; the practical default for us |
| **MXFP4** | ~4.25 | Block-scaled FP4 (shared block scale) — gpt-oss's native format | llama.cpp / vLLM | Running gpt-oss as shipped, minimal loss |
| **GPTQ** | 3–4 | Layer-wise error-minimizing PTQ using a calibration set (Hessian/OBQ-style) | GPU (vLLM, ExLlama) | Mature GPU serving, many pre-quantized models |
| **AWQ** | 4 | Activation-**aware**: protects the ~1% salient weight channels, then uniform low-bit; no iterative param update | GPU (vLLM) — often fastest | New GPU deployments; robust to calibration choice |
| **bitsandbytes** (NF4/FP4, LLM.int8) | 4 / 8 | **On-the-fly** quant at load (no pre-quant file); NF4 is the QLoRA base | HF Transformers | Dev/training; the only one that supports **QLoRA** training |
| *(others)* EXL2/EXL3, HQQ, SmoothQuant | 2–8 | mixed / calibration-free / activation-smoothing | ExLlama, etc. | niche/perf-tuning |

**Quality ordering (one benchmark, workload-dependent):** bitsandbytes showed the smallest
quality drop, GGUF second, with AWQ and GPTQ close behind. Treat rankings as *indicative* — the
right choice depends on your runtime and task. ([PremAI guide](https://www.premai.io/blog/llm-quantization-guide-gguf-vs-awq-vs-gptq-vs-bitsandbytes-compared-2026/), [pdpspectra](https://pdpspectra.com/blog/quantization-gptq-vs-awq-vs-bitsandbytes/), [vLLM guide](https://jarvislabs.ai/blog/vllm-quantization-complete-guide-benchmarks))

### GGUF k-quants (our runtime) in practice
- `Q4_K_M` — the universal default: ~72% memory saving, ~92–95% of quality retained.
- `Q5_K_M` — the common "sweet spot": +15–20% memory over Q4 for a safety margin on **code and
  reasoning** (relevant to us).
- `Q6_K` — near-lossless; `Q8_0` — effectively lossless, largest.
- **imatrix** (importance matrix): calibrate quantization with statistics from real prompts via
  `llama-imatrix`; essential below 4 bits and beneficial even for K-quants. ([llama.cpp quantize docs](https://github.com/crc-org/llama.cpp/blob/main/tools/quantize/README.md), [Morgann Riu](https://morgannriu.fr/en/blog/quantification-gguf-q4-q5-q8-choisir), ["Which Quantization Should I Use?"](https://arxiv.org/html/2601.14277v1))

## 3. The reasoning-model caveat (matters for gpt-oss)

gpt-oss is a **reasoning** model (Harmony analysis/commentary/final channels; it "thinks" before
answering). Empirical work shows **aggressive quantization can hurt reasoning/chain-of-thought
disproportionately** — more than a perplexity number would suggest. ([Quantization Hurts
Reasoning?](https://arxiv.org/pdf/2504.04823)) So: **do not judge a gpt-oss quant by perplexity
alone** — measure it on *reasoning/agent* tasks (our eval harness), because a quant that looks
fine on perplexity can quietly degrade multi-step tool use.

## 4. Fine-tuning-time quantization (LoRA / QLoRA) — only if you adapt gpt-oss

- **LoRA** — freeze the base, train low-rank adapter matrices on your data; tiny, cheap,
  composable. Base model unchanged.
- **QLoRA** — load the base in **4-bit NF4** (bitsandbytes), keep it frozen, train LoRA adapters
  in bf16; adds **double quantization** + **paged optimizers** so a 20B fits on a modest GPU.
  It's a *training-memory* technique, not a deployment quant. Unsloth documents QLoRA fine-tuning
  for gpt-oss specifically. ([Unsloth fine-tune tutorial](https://unsloth.ai/docs/models/gpt-oss-how-to-run-and-fine-tune/tutorial-how-to-fine-tune-gpt-oss))
- **QA-LoRA** — quantization-**aware** LoRA so the *merged* result stays quantized (avoids the
  "merge LoRA → back to fp16" blowup). ([QA-LoRA](https://arxiv.org/pdf/2309.14717))

For our read-only/edit code agent we likely **don't need fine-tuning** — the win is grounding
(navigate + LSP) and prompt/format quality, not new weights. QLoRA becomes relevant only if we
want gpt-oss to internalize a house style or a private codebase's conventions.

## 5. How to measure "performance loss" (and pick the best quant)

Three complementary signals — **never rely on just one**:

1. **Perplexity** (`llama-perplexity` on a held-out corpus): cheap sanity check; lower Δ = better.
2. **KL-divergence vs the FP16/native reference** on the *same* prompts: a stricter, more
   faithful measure of "how different is this quant's distribution from the original" than raw
   perplexity — the metric to trust when perplexities look similar.
3. **Downstream/agent task accuracy** — run the **evaluation harness** (see
   `evaluation-and-trustworthiness.md`): groundedness, correct-symbol resolution, tool-call
   validity, task success. This catches reasoning degradation that (1)–(2) miss.
Plus the operational axes: **tokens/sec** and **VRAM/RAM**.

### Recommended experiment for this project
Point everything at gpt-oss-20b via llama.cpp and sweep quants — **upward** from the native
4-bit, since going below rarely helps a reasoning model:

| Variant | What it tests |
|---|---|
| **MXFP4 (native)** | the shipped baseline |
| **Q5_K_M (+imatrix)** | small quality margin for code/reasoning |
| **Q6_K** | near-lossless, memory cost |
| **Q8_0** | effectively lossless reference upper bound |

For each: record Δperplexity, KL-vs-Q8/native, **agent task score**, tokens/sec, VRAM. Pick the
**knee** — the smallest/fastest variant whose *agent task score* (not just perplexity) is within
your tolerance of the Q8 reference. Expectation for gpt-oss: **native MXFP4 is already close to
optimal**; Q5_K_M/Q6_K buy marginal quality for real memory; sub-4-bit is not worth it.

## 6. Bottom line for us
- gpt-oss-20b is **natively MXFP4** — you're not "quantizing from FP16," you're choosing whether
  to *keep* 4-bit or *upcast* for a little quality.
- Use **llama.cpp GGUF** (our runtime); start at **MXFP4 / Q5_K_M with imatrix**; validate on the
  **agent eval harness**, not perplexity alone (reasoning caveat).
- Reach for **QLoRA** only to *adapt* gpt-oss to private data — a separate, later track.

## 5 W's — the decision in one frame

**WHAT** — Not "shrink gpt-oss from FP16" — it *ships* at 4-bit (MXFP4). The real decision is:
**keep native MXFP4, or upcast to Q5_K_M / Q6_K / Q8_0 (GGUF) for a little more quality at a
memory cost** — and, separately, whether to ever *fine-tune* with QLoRA. Quantization (deploy)
≠ LoRA/QLoRA (train).

**WHY** — To fit gpt-oss in the box's VRAM and run it fast **without losing reasoning quality**.
Because it's a reasoning model, over-aggressive quant degrades multi-step tool use more than a
perplexity number reveals — so the goal is *quality-preserving efficiency, measured on agent tasks*.

**WHO** — Us: **single-user, fully-local, on the SRMAP GPU box, via llama.cpp**. Not multi-tenant
cloud serving — so GGUF (our runtime) matters more than GPTQ/AWQ/vLLM, which are for GPU fleets.

**WHERE** — In **llama.cpp at model load** (the GGUF file's quant type), bounded by the box's
VRAM/RAM. QLoRA, if ever used, lives in a **separate fine-tuning step**, not in deployment.

**WHEN** — Pick a quant **once per model + hardware**; revisit only if VRAM changes or you swap
models. Reach for **QLoRA only** to teach gpt-oss private data/conventions — a distinct track, not
this deployment.

**HOW (next)** — Sweep **MXFP4 → Q5_K_M (+imatrix) → Q6_K → Q8_0**; record Δperplexity, KL-vs-Q8,
**agent-task score** (the eval harness), tokens/sec, VRAM; pick the **knee by agent-task score, not
perplexity**. Expectation: native MXFP4 is already near-optimal for gpt-oss.

## Sources
- [Unsloth — gpt-oss run & fine-tune](https://unsloth.ai/docs/models/gpt-oss-how-to-run-and-fine-tune) · [Unsloth — fine-tune tutorial](https://unsloth.ai/docs/models/gpt-oss-how-to-run-and-fine-tune/tutorial-how-to-fine-tune-gpt-oss)
- [IntuitionLabs — gpt-oss-20b hardware](https://intuitionlabs.ai/articles/hardware-requirements-gpt-oss-20b) · [llama.cpp — running gpt-oss](https://github.com/ggml-org/llama.cpp/discussions/15396) · [unsloth/gpt-oss-20b-GGUF](https://huggingface.co/unsloth/gpt-oss-20b-GGUF)
- [PremAI — GGUF vs AWQ vs GPTQ vs bitsandbytes](https://www.premai.io/blog/llm-quantization-guide-gguf-vs-awq-vs-gptq-vs-bitsandbytes-compared-2026/) · [pdpspectra — GPTQ vs AWQ vs bnb](https://pdpspectra.com/blog/quantization-gptq-vs-awq-vs-bitsandbytes/) · [vLLM quantization guide](https://jarvislabs.ai/blog/vllm-quantization-complete-guide-benchmarks)
- [llama.cpp quantize README](https://github.com/crc-org/llama.cpp/blob/main/tools/quantize/README.md) · [Which Quantization Should I Use? (arXiv)](https://arxiv.org/html/2601.14277v1) · [GGUF quant choice — Morgann Riu](https://morgannriu.fr/en/blog/quantification-gguf-q4-q5-q8-choisir)
- [Quantization Hurts Reasoning? (arXiv)](https://arxiv.org/pdf/2504.04823) · [QA-LoRA (arXiv)](https://arxiv.org/pdf/2309.14717)
