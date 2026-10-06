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

## Reference — [`research/`](research/)

| Doc | What it covers |
|-----|----------------|
| [gpt-oss-doc.md](research/gpt-oss-doc.md) | Running gpt-oss fully local + the Harmony response format — **required reading for the constrained-decoding work** |

> **Removed 2026-10-05.** The earlier research notes (`evaluation-plan.md`,
> `evaluation-and-trustworthiness.md`, `claude-internal-structure.md`,
> `literature-survey.xlsx`) encoded the abandoned ICDEC-2026 framing and are superseded by
> the review and build plan below. They remain recoverable from git history at `b069b6e`.
> Note that `design/build-plan.md` and `plans/write-tools-build-plan.md` still cite
> `claude-internal-structure.md` as design provenance; those citations are kept as a record
> of where the architecture came from, and now resolve only through git history.

## Build plans — [`plans/`](plans/)

| Doc | What it covers |
|-----|----------------|
| [bash-upgrade.md](plans/bash-upgrade.md) | Persistent same-shell bash, env-aware execution, unrestricted local mode |
| [write-tools-build-plan.md](plans/write-tools-build-plan.md) | The permission-gated write tier (edit / write / multi_edit) |

## Paper

| Doc | What it covers |
|-----|----------------|
| [review/2026-09-30-audit-and-research-review.md](review/2026-09-30-audit-and-research-review.md) | Independent audit of the tool layer and architecture, plus the fresh research assessment and ranked novelty options |
| [plans/conference-paper-build-plan.md](plans/conference-paper-build-plan.md) | **The current plan** — contributions, methods, phased build, experiment design, bibliography, timeline |
| [architecture/paper/](architecture/paper/) | Print-ready paper figures (system, channel-scoped constrained decoding, dispatch surface) + captions |

The manuscript directory does not exist yet; the template choice is still open (see §12 of the
build plan).
