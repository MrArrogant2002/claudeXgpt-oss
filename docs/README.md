# Documentation

Design notes and build plans for CodeBrain. For **installation and usage**, see the
[run guide](../README.md) at the repo root.

**New here?** Start with [project-construction-notes.md](project-construction-notes.md) —
a from-scratch build guide: dependencies, llama.cpp, the tokenizer vocab, the model,
what every component does, and the order it was built in.

## Reference

| Doc | What it covers |
|-----|----------------|
| [harmony-reference.md](harmony-reference.md) | Running gpt-oss at the token level and the Harmony response format — the protocol this agent renders and parses by hand |

## Design — [`design/`](design/)

| Doc | What it covers |
|-----|----------------|
| [build-plan.md](design/build-plan.md) | Overall architecture and the build milestones |
| [CLI-design.md](design/CLI-design.md) | The terminal UI spec |
| [user-interface-design-plan.md](design/user-interface-design-plan.md) | TUI design: streaming, input, theme |

## Plans — [`plans/`](plans/)

| Doc | What it covers |
|-----|----------------|
| [bash-upgrade.md](plans/bash-upgrade.md) | Persistent same-shell execution and env-aware commands |
| [write-tools-build-plan.md](plans/write-tools-build-plan.md) | The permission-gated write tier (edit / write / multi_edit) |
