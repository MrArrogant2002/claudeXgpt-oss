# Local Code Agent — Rust port (Stack A)

The same agent as the Python version, as a **single native binary**: gpt-oss brain
via llama.cpp, Harmony rendered here, in-process `glob`/`grep`/`read` (no subprocess),
`reqwest` to `llama-server`. Behaviour mirrors the proven Python agent.

> Built on a machine without a Rust toolchain, so it has **not been compiled here**.
> Compile it on the machine that runs the model (which has `cargo`). If anything
> fails to build, see **[If it doesn't compile](#if-it-doesnt-compile)** — the few
> assumptions are listed there.

## Layout

```
rust/
├── Cargo.toml
└── src/
    ├── main.rs         # CLI + REPL
    ├── config.rs       # env-configurable settings
    ├── harmony.rs      # render/parse via the openai-harmony crate
    ├── inference.rs    # reqwest -> llama-server /completion (token IDs in/out)
    ├── sandbox.rs      # path containment
    ├── context.rs      # result budgeting + drop stale CoT
    ├── agent_loop.rs   # orchestration + empty-final & overflow recovery
    └── tools/
        ├── mod.rs      # Tool trait + registry
        ├── glob.rs     # ignore + globset
        ├── grep.rs     # ignore + regex
        └── read.rs     # std::fs + line range (accepts param aliases)
```

## Build & run (on the model machine)

Prereqs: a recent Rust toolchain (`rustup`), plus `llama-server` + the gpt-oss GGUF.

```bash
cd rust
cargo build --release
```

Start the server (raw completion, **no `--jinja`**; big context):

```bash
llama-server -m /path/to/gpt-oss-20b.gguf -c 32768 --port 8081 -ngl 999
```

Run:

```bash
# one-shot
./target/release/codeagent --project ../requests "where is hooks.py and what does it do?"

# interactive
./target/release/codeagent --project ../requests

# or during development
cargo run --release -- --project ../requests "walk me through how hooks works"
```

Flags: `--project DIR`, `--reasoning low|medium|high`, `--show-reasoning`, `--quiet`.
Final answer → stdout; tool trace/reasoning → stderr (same split as the Python CLI).
Env vars are identical to the Python agent (`AGENT_BASE_URL`, `AGENT_REASONING`,
`AGENT_MAX_TOKENS`, `AGENT_TEMPERATURE`, `AGENT_MAX_TURNS`, `AGENT_TOOL_RESULT_CAP`,
`AGENT_READ_DEFAULT_LINES`, `AGENT_TIMEOUT`).

## First build note

The first `cargo build` pulls `openai-harmony` from git and compiles the dep tree —
give it a few minutes. The first *run* downloads the o200k_harmony vocab (cached after).

## If it doesn't compile

I wrote this against the exact `openai/harmony` Rust source, but couldn't compile it
here. The only likely friction points, and their one-line fixes:

1. **`HarmonyEncoding` import path** (`src/harmony.rs`). If `use openai_harmony::HarmonyEncoding;`
   fails, the type is re-exported elsewhere — check `cargo doc -p openai-harmony` and
   adjust the path (e.g. `openai_harmony::encoding::HarmonyEncoding`).
2. **Token type `Rank` vs `u32`.** The code assumes the crate's token `Rank` is a
   `u32` alias (it is, via tiktoken). If the compiler complains about `Vec<u32>` vs
   `Vec<Rank>` in `src/harmony.rs`, add `use openai_harmony::Rank;` and replace the
   `u32`s in that file with `Rank` (and convert the JSON token parse in
   `src/inference.rs` with `as u32`/`into()` accordingly).
3. **`openai-harmony` version.** Cargo.toml uses the git dependency from the README.
   If you want to pin a crates.io release instead, set `openai-harmony = "<version>"`.

Everything else (reqwest, ignore, globset, regex, serde_json, anyhow) is standard.

## Parity with the Python agent

Same loop, same recovery, same tools and defaults:
- glob → grep → read funnel; serial tool execution
- empty-final recovery (drop reasoning, escalate tokens if truncated, nudge; bounded)
- context-overflow recovery (drop reasoning, retry once; then a clear error)
- read accepts `start_line/line_start/start` + `end_line/line_end/end`, caps bare reads
- result budgeting (`AGENT_TOOL_RESULT_CAP`), stale-CoT dropping between turns
- sandbox blocks `..`, absolute paths, and symlink escapes

Difference from Python: grep/glob run **in-process** (ignore/globset/regex crates)
instead of shelling out to ripgrep — one binary, no external `rg` needed.
