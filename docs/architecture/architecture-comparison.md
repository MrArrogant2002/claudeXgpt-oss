# Architecture Comparison — our local agent vs OpenCode, OpenRouter, DeepSeek Harness

A grounded comparison of our fully-local code agent against three reference systems,
a prioritized gap analysis, and a 5 W's report. Competitor details were gathered from
their public sites/repos (Sept 2026) and will evolve; DeepSeek Harness is in developer
preview.

## Framing: only one is a true peer

The three systems are different *kinds* of thing, so the comparison isn't apples-to-apples:

- **[OpenCode](https://opencode.ai/)** — a coding *agent* (a Claude Code alternative). **Our true peer.**
- **[OpenRouter](https://openrouter.ai/)** — an LLM API *gateway/router* (infrastructure, not an agent). Cloud-first; ~80% of it is a non-goal for us by design.
- **[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)** — a plugin-based *agent harness* (TypeScript, preview). An **extensibility pattern** to learn from.

So the useful question is: *what transferable ideas do these carry that our local agent lacks?*

## Our architecture (baseline)

Fully-local, offline code-understanding agent: **gpt-oss-20b via llama.cpp** raw `/completion`,
**Harmony rendered client-side** (token IDs in/out), **navigate-don't-index** tools
(`list_dir` → `glob` → `grep` → `read`, plus opt-in `bash` for compile/lint/test),
**read-only** by default, a **stdlib+prompt_toolkit TUI** (streaming, history, autocomplete,
instant Ctrl-C), single agent / serial tools / one context window with compaction, and
recovery layers (malformed-header salvage, leaked-tool-call recovery, empty-final and
context-overflow recovery, token accounting). No network, no index, nothing leaves the box.

## Side-by-side

| Dimension | OpenCode | OpenRouter | DeepSeek Harness | **Ours** |
|---|---|---|---|---|
| Kind | Coding agent | LLM API gateway/router | Plugin agent harness (preview) | Local code-Q&A agent |
| Locality | Cloud + local | Cloud only | Local dev | **Fully local / offline** |
| Models | 75+ providers | 400+ routed | pluggable | **one** (gpt-oss via llama.cpp) |
| Provider abstraction / fallback | yes | *its whole point* | plugin | **none** (one hard-wired endpoint) |
| Code intelligence | **LSP** (types, defs, refs) | n/a | plugins | **text-level** (grep/read) |
| Tool extensibility | MCP | server-tools | everything-is-a-plugin | fixed in-process registry |
| Edits code | yes | n/a | via plugins | **read-only** + opt-in `bash` |
| Architecture | **client/server**, multi-client | API gateway | plugin core | monolith TUI+engine (clean `run_turn`/`on_event` seam) |
| Front-ends | TUI + desktop + IDE | SDKs/integrations | web UI + CLI | TUI + CLI |
| Multi-session | yes | n/a | — | single |
| Privacy | "stores no code" | zero-retention option | local | **strongest — never leaves the box** |

## What we're missing (prioritized; tagged by fit with our goals)

1. **Semantic code intelligence via LSP** — *OpenCode; highest value.* We navigate with regex
   `grep` + `read`; OpenCode loads a language server for accurate go-to-definition,
   find-references, and type/signature info. For "explain an unknown codebase without
   hallucinating," this is the single biggest quality lever — and LSP servers run **locally**,
   so it fits our offline rule. **→ Detailed plan: `lsp-tool-build-plan.md`.**
2. **Provider / inference abstraction + fallback** — *OpenRouter + OpenCode.* We're welded to one
   llama.cpp endpoint. An abstraction layer would let us swap or fall back between local models
   (e.g., a small fast model for tool turns, a bigger one for synthesis). Medium; stays local.
3. **Client/server split** — *OpenCode.* A headless agent *core* that a TUI, IDE extension, or web
   UI attaches to. We're a monolith, but half-way there: `run_turn` + `on_event` is a clean seam a
   socket/HTTP layer could expose. Medium.
4. **Plugin/extension architecture** — *DeepSeek Harness.* Our `Registry` is a mini tool-plugin
   system; a fuller version would make *models, memory, and UIs* pluggable too. Low-medium.
5. **Edit/write tier + real permission model** — *OpenCode.* We stayed read-only (+ opt-in `bash`);
   editing is the natural next capability and needs proper allow/deny/confirm. Medium.
6. **MCP / external tool ecosystem** — *OpenCode.* Skipped for in-process purity; MCP servers *can*
   be local, so not off-limits, but low priority.

**Non-goals we're "missing" but shouldn't chase** (they contradict fully-local / private /
single-user): cloud provider routing, session-sharing links, cost/billing, multimodal,
SSO/enterprise, telemetry. OpenRouter is mostly this.

## 5 W's report

**WHAT** — The real gaps are LSP semantic intelligence (1), model abstraction/fallback (2),
a headless client/server core (3), pluggability (4), and an edit tier + permissions (5).
Everything else is either infrastructure we don't want or minor.

**WHY** — Only (1) meaningfully improves our *core job* (grounded answers on unknown code) —
`grep` can't resolve which `send` among many, follow inheritance, or read a type the way an LSP
can, and that's exactly where the model currently guesses. (2)–(5) improve *flexibility and
reach*, not answer quality, so they're second-tier for a research/personal tool.

**WHO** — OpenCode serves professional devs wanting a polished, model-agnostic, editing,
IDE-grade agent. OpenRouter serves app builders needing many cloud models behind one API.
DeepSeek Harness serves tinkerers building custom agents. **We serve a narrower, sharper niche:
the offline / air-gapped / privacy-hard / single-box, one-local-model user** — where none of the
three fully work (they assume internet, multiple providers, or a services stack).

**WHERE** — We sit at the **fully-local, Harmony-native, navigate-don't-index** corner. That's a
genuine moat: no index to build or keep fresh, no data leaves the machine, raw token-level
control. The others trade that away for breadth. Our weaknesses are *depth of code understanding*
(no LSP) and *breadth of models* (one).

**WHEN** — Adopt in this order, each triggered by real need: **LSP now** (quality unlock, stays
local); **provider abstraction** when you want more than gpt-oss or a fast/slow split;
**client/server** when you want an IDE or web front-end; **edit tier + permissions** when moving
from Q&A to changes; **plugins/MCP** only if an ecosystem materializes.

**HOW (next step)** — Prototype **#1 (LSP)** first: a tool that talks to a local language server
over stdio JSON-RPC to answer "where is X defined / who calls X / what's its type," then feeds
those precise locations into the existing `read` funnel. Local, highest accuracy win, composes
with what we have. Then **#3**: expose `run_turn` over a small local socket so the TUI becomes one
client of a reusable core. Full design for #1 is in `lsp-tool-build-plan.md`.
