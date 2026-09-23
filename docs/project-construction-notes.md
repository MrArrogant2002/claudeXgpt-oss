# Project construction notes — Local Code Agent (gpt-oss + llama.cpp)

A from-scratch build guide + construction log + reading list for this project: an
**offline, Claude-Code-style code-understanding & editing agent** driven by
**gpt-oss-20b** served locally by **llama.cpp**, with the **OpenAI Harmony** response
format rendered/parsed by us. Everything runs locally — no third-party services.

---

## 1. What this project is (and what's involved)

A single-agent, tool-augmented **ReAct loop**: the model reasons, calls tools (search,
read, run, edit), observes results, and repeats until it can answer. There is **no agent
framework** (no LangChain/LangGraph/CrewAI) — it's hand-rolled Python talking to the raw
llama.cpp `/completion` endpoint.

Pieces involved:

- **Model + server:** gpt-oss-20b (GGUF, MXFP4) served by `llama-server` (llama.cpp).
- **Protocol:** OpenAI Harmony chat format, rendered/parsed client-side via `openai-harmony` + the o200k_base tokenizer vocab.
- **Agent core:** an orchestration loop, an inference client, a Harmony codec, context/compaction, config.
- **Tools:** `list_dir`, `glob`, `grep`, `read` (read-only funnel); `bash` (opt-in exec); `edit`/`write`/`multi_edit` (permission-gated write tier).
- **Safety:** a path sandbox + a Claude-style permission engine.
- **Project memory:** `local init` (TUI `/init`) → `local_mind.md` (offline CLAUDE.md), auto-injected into queries.
- **Front-end:** `tui.py` (interactive Claude-Code-style TUI).
- **Evaluation:** an eval harness + benchmark to be assembled against small real repos (the earlier synthetic fixture was removed).

---

## 2. Hardware & the two-machine workflow

- **Build machine** (no GPU/model needed): edit code, run offline compile/logic tests. *Never run model-dependent code here.*
- **GPU box** (SRMAP): runs `llama-server` + the agent. gpt-oss-20b fits a **≤16 GB VRAM** GPU because it ships MXFP4 (~13 GB).
- Sync by `git push` (build) → `git pull` (box). This is why the tokenizer vocab is downloaded once per machine and the config is env-overridable.

---

## 3. Dependencies & installation (step by step)

### 3.1 System prerequisites
| Tool | Why | Notes |
|------|-----|-------|
| **Python 3.10+** | runs the agent | 3.12 used in dev; `X | None` syntax needs ≥3.10 |
| **git** | two-machine sync | |
| **ripgrep (`rg`)** | fast `grep` tool | optional — falls back to a pure-Python walk |
| **Node.js** | run/test JS/TS in a target repo | optional |
| **Go toolchain** | run/test Go in a target repo | optional |

### 3.2 The model — gpt-oss-20b (GGUF, MXFP4)
Download one GGUF (~13 GB) from Hugging Face, e.g. `ggml-org/gpt-oss-20b-GGUF` or
`unsloth/gpt-oss-20b-GGUF`. **Keep the native MXFP4 weights — do not re-quantize**
(it's quantization-aware trained; re-quantizing costs tool-call accuracy). See
[model-serving-strategy](#) and `docs/research/quantization-research.md`.

### 3.3 llama.cpp / `llama-server`
Two options:
- **Prebuilt binaries** (easiest): grab a recent CUDA build from the llama.cpp GitHub releases (`ggml-org/llama.cpp`).
- **Build from source** (for your exact GPU):
  ```bash
  git clone https://github.com/ggml-org/llama.cpp && cd llama.cpp
  cmake -B build -DGGML_CUDA=ON
  cmake --build build --config Release -j
  # llama-server ends up in build/bin/
  ```
Start it (note: **no `--jinja`** — we render Harmony ourselves; port 8081):
```bash
llama-server -m /path/to/gpt-oss-20b.gguf --port 8081 -ngl 999 \
  -c 65536 -fa on -ctk q8_0 -ctv q8_0
```
- `-ngl 999` all layers on GPU · `-c 65536` context (32k min, 64k comfortable) · `-fa on -ctk q8_0 -ctv q8_0` = flash attention + 8-bit KV cache (the biggest memory lever). Add `--n-cpu-moe 4` for `-c 131072` if you have room.

### 3.4 Python packages
```bash
pip install -r requirements.txt        # openai-harmony, requests
pip install -r requirements-ui.txt     # prompt_toolkit>=3.0  (optional, richer TUI input)
```
That's the entire runtime dependency surface — everything else is Python stdlib.

### 3.5 The Harmony tokenizer vocab (offline, one-time)
`openai-harmony` needs the **o200k_base** BPE vocab (~3.6 MB) to map text ⇄ token IDs.
By default it *downloads* it on first use; for offline boxes we vendor it. Download once:
```bash
curl -L -o vendor/tiktoken/o200k_base.tiktoken \
  https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken
```
- Expected size **3,613,922 bytes**, sha256 `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d`.
- Loader: [`agent/harmony_codec.py`](../agent/harmony_codec.py) verifies the hash and points `TIKTOKEN_RS_CACHE_DIR` at it; see [`vendor/tiktoken/README.md`](../vendor/tiktoken/README.md). Committed as binary via `.gitattributes` so git can't corrupt it.

### 3.6 Verify the setup
```bash
llama-server ... --port 8081        # terminal 1
python tui.py --project ./your-repo  # terminal 2  → banner shows "llama-server connected"
```
If the tokenizer vocab is missing or the server is down, `tui.py` prints the exact fix.

---

## 4. Components (architecture map, with file pointers)

```
llama-server (gpt-oss-20b, MXFP4)  ◀── HTTP /completion (token IDs) ──┐
                                                                      │
  agent/inference.py   — HTTP client: send token IDs, get tokens back, usage/streaming
  agent/harmony_codec.py — render prompt ⇄ parse channels; offline vocab; salvage
  agent/loop.py        — the ReAct loop: render→complete→parse→dispatch→recover
  agent/context.py     — history budgeting, drop stale reasoning
  agent/compact.py     — summarize old turns when the window fills (M5)
  agent/config.py      — all knobs (env-overridable): server, sampling, limits
  agent/sandbox.py     — path jail (no escaping the project root)
  agent/permissions.py — Claude-style permission engine (plan/ask/accept/…)
  agent/edits.py       — read-before-write freshness, atomic writes, diffs
  agent/project_mind.py— local_mind.md build/inject + staleness (local init)
  agent/tools/         — list_dir, glob, grep, read, bash, edit/write/multi_edit
  agent/ui/            — app.py (REPL), render.py, theme.py, banner.py, session.py
  tui.py               — the interactive TUI front-end
```

The **funnel** is the core navigation idea: `list_dir` (orient) → `glob` (locate files)
→ `grep` (find symbols) → `read` (read the lines) → follow references. It *navigates,
doesn't index* — no embeddings, no vector DB.

---

## 5. Key concepts

- **Harmony format:** gpt-oss emits typed channels — `analysis` (chain-of-thought),
  `commentary` (tool calls), `final` (the answer) — delimited by special tokens
  (`<|channel|>`, `<|message|>`, `<|call|>`, `<|return|>`, …). We render the whole
  conversation to **token IDs** ourselves and parse the raw completion back into
  channels; we deliberately use the **raw `/completion`** endpoint (not
  `/v1/chat/completions`) so the server doesn't apply its own template on top of ours.
- **ReAct loop + recovery:** the model often mis-formats output (duplicated tool-call
  headers, tool calls "leaked" into reasoning, analysis-only turns with an empty final).
  The loop has layered recovery: malformed-header salvage, leaked-call inference,
  empty-final nudging, a **tool-less synthesis fallback**, context-overflow recovery, and
  compaction. This robustness layer is most of what makes a 20B model usable as an agent.
- **Permission engine:** deny-wins, fail-closed; modes `plan|default|acceptEdits|bypassPermissions|dontAsk`; sensitive paths always denied. The sandbox is the hard wall.
- **Serving strategy:** keep MXFP4 weights; the real levers are **8-bit KV cache**,
  **context length**, correct **Harmony template**, and **sampling** (send `top_p`
  alongside `temperature`). Never push router/attention below 8-bit.

---

## 6. The construction process (order it was built + how each step was verified)

The code carries milestone markers (M0–M5); later capabilities were layered on top.

| Stage | What it added | Verified by |
|-------|---------------|-------------|
| **M0 — inference** | `inference.py`: talk to llama.cpp raw `/completion`, get output token IDs (`return_tokens`), track usage | smoke test against a live server |
| **M1 — Harmony codec** | `harmony_codec.py`: render a conversation to token IDs; parse a completion into channels; offline vocab loader | round-trip render/parse; offline vocab hash check |
| **M2 — read-only tools + sandbox** | `read`, `list_dir` + `sandbox.py` path jail | unit tests on a fixture repo |
| **M3 — the funnel** | `glob`, `grep` (ripgrep + py fallback), tool registry | fixture searches |
| **M4 — the loop** | `loop.py`: dispatch tool calls, feed results back, circuit-breaker, error-as-data | mocked `run_turn`; live runs |
| **M5 — long sessions + UI** | context budgeting, compaction, streaming, the TUI (`agent/ui/`) | mocked streaming; live TUI |
| **Exec** | `bash` tool (opt-in, deny-list, timeouts) for compile/lint/test | fixture with a failing test |
| **Write tier** | `edit`/`write`/`multi_edit` + `permissions.py` + `edits.py` (atomic, backups, freshness) | permission-gate + overwrite-guard tests |
| **TUI polish** | permission modes, `/`-commands, streaming, espresso theme | offline render tests |
| **Project memory** | `project_mind.py`: `local init` → `local_mind.md`, auto-inject, staleness | mocked run_turn injection tests |
| **Robustness fixes** | tokenizer salvage hardening, tool accuracy (glob ignore/recency, grep context, read binary), tool-less synthesis fallback | offline mock suites |

> Scope note: an LSP tool and a headless CLI + synthetic benchmark were built and later
> removed to focus the system; the paper's evaluation harness is being rebuilt against
> small real repositories.

### Offline verification methodology (the two-machine discipline)
On the build machine we can't run the model, so every change is checked with:
1. `py_compile` (syntax) + import (name resolution).
2. **Mocked `run_turn`** — patch `harmony_codec.render/parse` + `inference.complete` to
   drive the loop deterministically (no server). This is how recovery paths, the mind
   injection, and the synthesis fallback were verified.
3. Real filesystem I/O for the tools (sandbox, glob/grep/read) against a scratch repo.
Live model runs happen only on the GPU box after `git pull`.

---

## 7. How to run (recap)

```bash
# on the box, model up (see 3.3)
python tui.py --project ./repo                              # interactive; type questions
python tui.py --project ./repo --allow-exec --allow-edit   # + run/edit code; /init builds local_mind.md
```
Key env knobs (`agent/config.py`): `AGENT_BASE_URL`, `AGENT_TEMPERATURE`/`AGENT_TOP_P`,
`AGENT_MAX_TOKENS`(+`_CAP`), `AGENT_REASONING`, `AGENT_CONTEXT_TOKENS`, `AGENT_LOCAL_MIND`.

---

## 8. Strongly related papers (to deepen your understanding)

Grouped by relevance to this system. *arXiv ids given where confident — confirm by title.*

### Agent loop & reasoning (the core pattern here)
- **ReAct: Synergizing Reasoning and Acting in Language Models** — Yao et al., 2022 (arXiv:2210.03629). *The reason→act→observe loop this agent implements.*
- **Chain-of-Thought Prompting Elicits Reasoning in LLMs** — Wei et al., 2022 (arXiv:2201.11903). *Why the `analysis` channel exists.*
- **Reflexion: Language Agents with Verbal Reinforcement Learning** — Shinn et al., 2023 (arXiv:2303.11366). *Self-correction via feedback — kin to our recovery/nudge layers.*
- **Self-Refine: Iterative Refinement with Self-Feedback** — Madaan et al., 2023 (arXiv:2303.17651).

### Tool use & function calling
- **Toolformer: LMs Can Teach Themselves to Use Tools** — Schick et al., 2023 (arXiv:2302.04761).
- **ToolLLM: Mastering 16000+ Real-World APIs** — Qin et al., 2023 (arXiv:2307.16789).
- **Gorilla: LLM Connected with Massive APIs** — Patil et al., 2023 (arXiv:2305.15334). *Also see the Berkeley Function-Calling Leaderboard — our #1 metric (tool-call validity).*

### Code agents & benchmarks (closest to what we're building)
- **SWE-bench: Can LMs Resolve Real-World GitHub Issues?** — Jimenez et al., 2023 (arXiv:2310.06770). *The gold benchmark for repo-level code agents.*
- **SWE-agent: Agent-Computer Interfaces Enable Automated SWE** — Yang et al., 2024 (arXiv:2405.15793). *How tool/interface design drives agent success — directly relevant to our funnel + tools.*
- **Executable Code Actions Elicit Better LLM Agents (CodeAct)** — Wang et al., 2024 (arXiv:2402.01030). *Acting via executable code — relates to our `bash` tier.*
- **Evaluating LLMs Trained on Code (Codex/HumanEval)** — Chen et al., 2021 (arXiv:2107.03374).
- **Program Synthesis with LLMs (MBPP)** — Austin et al., 2021 (arXiv:2108.07732).

### Autonomous agents & memory
- **Voyager: An Open-Ended Embodied Agent with LLMs** — Wang et al., 2023 (arXiv:2305.16291). *Skill/memory accumulation — conceptual kin to `local_mind.md`.*
- **Generative Agents: Interactive Simulacra of Human Behavior** — Park et al., 2023 (arXiv:2304.03442). *Memory + reflection.*
- **The Rise and Potential of LLM-Based Agents: A Survey** — Xi et al., 2023 (arXiv:2309.07864). *Broad map of the field.*

### The model: Mixture-of-Experts
- **Mixtral of Experts** — Jiang et al., 2024 (arXiv:2401.04088). *Sparse MoE like gpt-oss.*
- **Switch Transformers** — Fedus et al., 2021 (arXiv:2101.03961).
- **GShard** — Lepikhin et al., 2020 (arXiv:2006.16668).
- **gpt-oss model card + OpenAI Harmony format** — OpenAI, 2025 (openai.com / the `openai-harmony` repo). *Primary source for the format we implement.*

### Quantization (why MXFP4 + KV-cache is the strategy)
- **GPTQ: Accurate Post-Training Quantization for GPTs** — Frantar et al., 2022 (arXiv:2210.17323).
- **AWQ: Activation-aware Weight Quantization** — Lin et al., 2023 (arXiv:2306.00978).
- **LLM.int8(): 8-bit Matrix Multiplication** — Dettmers et al., 2022 (arXiv:2208.07339).
- **QLoRA: Efficient Finetuning of Quantized LLMs** — Dettmers et al., 2023 (arXiv:2305.14314); and **LoRA** — Hu et al., 2021 (arXiv:2106.09685).
- **SmoothQuant** — Xiao et al., 2022 (arXiv:2211.10438).
- **Microscaling Data Formats for Deep Learning (MX / MXFP4)** — Rouhani et al., 2023 (arXiv:2310.10537). *The format gpt-oss ships in.*

### Retrieval contrast (why we navigate, not index)
- **Retrieval-Augmented Generation for Knowledge-Intensive NLP** — Lewis et al., 2020 (arXiv:2005.11401). *The approach we deliberately avoid for code — good to understand the trade-off.*

### Evaluation & trustworthiness (for the eval harness)
- **SelfCheckGPT: Zero-Resource Hallucination Detection** — Manakul et al., 2023 (arXiv:2303.08896). *Self-consistency to catch quant damage.*
- **G-Eval: NLG Evaluation using GPT-4 with Better Alignment** — Liu et al., 2023 (arXiv:2303.16634). *LLM-as-judge for open-ended answers.*
- **RAGAS: Automated Evaluation of RAG** — Es et al., 2023 (arXiv:2309.15217).
- **TrustLLM: Trustworthiness in Large Language Models** — Sun et al., 2024 (arXiv:2401.05561).

See also `docs/research/` in this repo (gpt-oss/Harmony, quantization, evaluation) for
the applied notes.
