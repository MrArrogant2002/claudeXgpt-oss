# Documentation

Design notes, research, and build plans for the Local Code Agent.
For **installation and usage**, see the [run guide](../README.md) at the repo root.

**New here?** Start with [project-construction-notes.md](project-construction-notes.md) —
a from-scratch build guide (dependencies, llama.cpp, tokenizer, model), what every
component does, the order it was built in, and a curated reading list of related papers.

## Architecture — [`architecture/`](architecture/)

| Doc | What it covers |
|-----|----------------|
| [architecture.svg](architecture/architecture.svg) | The headline overview figure (also `.drawio` source + per-view SVGs) |
| [architecture-comparison.md](architecture/architecture-comparison.md) | How this agent's architecture compares to Claude Code |

The editable source is [`architecture.drawio`](architecture/architecture.drawio); the
per-page figures are `architecture-containers.svg`, `-components.svg`, `-runtime.svg`,
and `-permission-model.svg`.

## Design — [`design/`](design/)

| Doc | What it covers |
|-----|----------------|
| [build-plan.md](design/build-plan.md) | The overall agent architecture and build milestones |
| [CLI-design.md](design/CLI-design.md) | The Claude Code–style terminal UI spec |
| [user-interface-design-plan.md](design/user-interface-design-plan.md) | The TUI design plan (streaming, input, theme) |

## Research — [`research/`](research/)

| Doc | What it covers |
|-----|----------------|
| [gpt-oss-doc.md](research/gpt-oss-doc.md) | Running gpt-oss fully local + the Harmony response format |
| [claude-internal-structure.md](research/claude-internal-structure.md) | Claude Code internals (memory, context, hooks, permissions) |
| [quantization-research.md](research/quantization-research.md) | GGUF quantization choices for gpt-oss |
| [evaluation-and-trustworthiness.md](research/evaluation-and-trustworthiness.md) | How to evaluate the agent on unknown codebases |
| [literature-survey.xlsx](research/literature-survey.xlsx) | Reference survey (spreadsheet) |

## Build plans — [`plans/`](plans/)

| Doc | What it covers |
|-----|----------------|
| [write-tools-build-plan.md](plans/write-tools-build-plan.md) | The permission-gated write tier (edit / write / multi_edit) |

## Paper — [`../research-paper/`](../research-paper/)

The ICDEC-2026 paper (Springer `sn-jnl` template): `main.tex` skeleton + `references.bib`.
See [`../proj-conf.md`](../proj-conf.md) for the paper plan.
