# Local Code Agent — Run Guide

A Claude Code–style code agent with **gpt-oss** as the brain, running fully local.
You render the **Harmony** prompt yourself and feed raw token IDs to **llama.cpp**'s
`/completion` endpoint — no chat template, no Ollama/vLLM wrapper.

This repo is built on one machine and run on another (the one with the model).
Below is how to run it on the **model machine**.

---

## Status: Milestone M0 + M1 (foundation)

Built so far:

| File | Role |
|------|------|
| `agent/config.py` | Settings (env-overridable) |
| `agent/harmony_codec.py` | Render conversation → token IDs; parse tokens → channels |
| `agent/inference.py` | llama.cpp raw `/completion` client (token IDs in/out) |
| `m0_smoke.py` | **Round-trip smoke test** — run this first |

Tools (glob/grep/read) and the full agent loop come next (M2–M4), **after** the
smoke test confirms the raw token round-trip works on your llama.cpp build.

---

## Prerequisites (on the model machine)

1. **Python 3.11+** and the deps:
   ```bash
   pip install -r requirements.txt
   ```
2. **gpt-oss GGUF + a recent llama.cpp build** (`llama-server`).
3. **ripgrep** (`rg`) on PATH — used later by the grep tool (optional for M0).

---

## Step 1 — start the llama.cpp server (raw completion)

```bash
llama-server -m /path/to/gpt-oss-20b.gguf -c 8192 --port 8081 -ngl 999
```

- **No `--jinja`** — that flag applies llama.cpp's own Harmony template to the
  *chat* endpoint. We render Harmony ourselves and use the raw `/completion`
  endpoint, so we must **not** let the server template anything.
- `-ngl 999` offloads all layers to GPU (lower it if VRAM is tight).
- `-c 8192` context size (raise toward the model's 131k if you have the memory).
- Port `8081` matches the default in `config.py` (8080 is often taken by Apache).

> Your llama.cpp must be recent enough to support `return_tokens` on `/completion`.
> If it isn't, the smoke test will say so clearly and we'll adapt the codec.

## Step 2 — run the M0 smoke test

```bash
python m0_smoke.py
```

or point it at a different server:

```bash
# Linux/macOS
AGENT_BASE_URL=http://localhost:8081 python m0_smoke.py
# Windows PowerShell
$env:AGENT_BASE_URL="http://localhost:8081"; python m0_smoke.py
```

**Expected output** (roughly):

```
[ok] server up at http://localhost:8081  ({'status': 'ok'})
[render] N prompt tokens; stop tokens = [200002, 200012]
[infer] M output tokens (stopped=...)

--- analysis ---
<the model's private reasoning>

--- final ---
2 + 2 = 4.

============================================================
[PASS] round-trip works — got a final answer.
```

---

## What to report back

Tell me which of these happened so we proceed correctly:

1. **`[PASS]`** with an `analysis` + `final` split → the round-trip works; I'll build the tools (M2–M3) next.
2. **`Server returned no output token IDs …`** → your llama.cpp lacks `return_tokens`; I'll switch the codec to tokenize the returned text instead.
3. **Only a `final` (no `analysis`), or empty final** → we tune reasoning effort / max tokens.
4. **Cannot reach server** → server/port issue (check Step 1).

Paste the console output and I'll take it from there.

---

## Configuration (env vars)

| Var | Default | Meaning |
|-----|---------|---------|
| `AGENT_BASE_URL` | `http://localhost:8081` | llama.cpp server root (no `/v1`) |
| `AGENT_REASONING` | `medium` | `low` \| `medium` \| `high` |
| `AGENT_MAX_TOKENS` | `2048` | max tokens generated per call |
| `AGENT_TEMPERATURE` | `0.7` | sampling temperature |
| `AGENT_MAX_TURNS` | `12` | tool-loop circuit breaker (used from M3) |
| `AGENT_PROJECT_ROOT` | cwd | folder the tools may access (used from M2) |
| `AGENT_TOOL_RESULT_CAP` | `30000` | max chars per tool result (used from M2) |

---

## Git workflow reminder

`.venv/`, `__pycache__/`, and `models/` are gitignored. Build here → `git push`
→ on the model machine `git pull` → `pip install -r requirements.txt` → run.
