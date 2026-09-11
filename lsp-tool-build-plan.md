# Build Plan — LSP Code-Intelligence Tool

Give the agent **semantic** code understanding — accurate "where is X defined", "who calls
X", and "what's X's type/signature" — by talking to a **local language server** over stdio
JSON-RPC, instead of guessing from `grep` text matches. This is the highest-value gap from
`architecture-comparison.md`, and it stays **100% local/offline** (language servers are local
processes), so it fits our design.

## 1. Why LSP beats grep (the motivation)

`grep` is text-level: it finds strings, not meaning. On a real codebase it can't tell *which*
`send` you mean, follow an import/alias to the real definition, resolve an inherited or
re-exported method, or tell you a symbol's type. That ambiguity is exactly where a 20B model
starts to hallucinate ("`Session.send` probably does X"). A language server answers precisely:

| Question | grep today | LSP |
|---|---|---|
| Where is `send` defined? | every line containing "send" | the one true definition (file:line) |
| Who calls `get_adapter`? | text matches (misses aliases) | real references / call sites |
| What does `send` return? | — (read + guess) | hover: signature + type + docstring |
| What's in this module? | read the whole file | document outline (symbols + ranges) |

LSP turns "read text and infer" into "resolve, then read the exact lines" — grounding answers
and cutting hallucination.

## 2. The key design bridge: names → positions

The model thinks in **symbol names**; most LSP requests need a **position** (`file`, line,
char). The bridge is **`workspace/symbol`**, which takes a *query string* (a symbol name) and
returns matching symbols with locations across the whole project — no position required. That's
the natural entry point for a name-oriented agent:

```
model: "where is Session.send?"  →  workspace/symbol("send")  →  [sessions.py:712 (Method)]
        → (optionally) hover/references at that location  →  return file:line to the model
        → model then read()s those exact lines
```

So the tool is name-in, `file:line`-out — and composes with the existing `read` funnel.

## 3. Tool design

A single `lsp` tool with an `op` selector (mirrors how `bash` takes a `command`). The model
supplies a symbol name and/or a file; we translate to LSP requests and return compact
`path:line: text` results — same shape as `grep`, so the loop/UI need no changes.

| `op` | Args | LSP request(s) | Returns |
|---|---|---|---|
| `defs` | `symbol` (+ optional `path`) | `workspace/symbol`, or `textDocument/definition` if a position is known | definition location(s) |
| `refs` | `symbol` (+ `path`) | `workspace/symbol` → `textDocument/references` (incl. declaration) | caller/reference locations |
| `info` | `symbol` (+ `path`) | `workspace/symbol` → `textDocument/hover` | signature + type + doc |
| `outline` | `path` | `textDocument/documentSymbol` | symbols + line ranges in a file |

Schema sketch:
```json
{
  "type": "object",
  "properties": {
    "op":     {"type": "string", "enum": ["defs", "refs", "info", "outline"]},
    "symbol": {"type": "string", "description": "symbol name (function/class/variable)"},
    "path":   {"type": "string", "description": "optional file to scope/disambiguate"}
  },
  "required": ["op"]
}
```
Result cap + budgeting reuse `context.budget`; paths reuse the sandbox's relativize.

## 4. LSP mechanics the implementation must handle

A language server is a separate process speaking **JSON-RPC 2.0** over stdio. The client must:

1. **Handshake** — spawn the server, send `initialize` (with `rootUri` = sandbox root +
   client capabilities), await result, send `initialized`.
2. **Open documents** — `textDocument/didOpen` with the file's text before position-based
   requests (servers operate on their in-memory view).
3. **Query** — send the request (`workspace/symbol`, `textDocument/definition`, `references`,
   `hover`, `documentSymbol`), correlate the response by `id`.
4. **Framing** — messages are `Content-Length: N\r\n\r\n<json>`; the reader must parse headers
   then read exactly N bytes.
5. **Async traffic** — servers push unsolicited **notifications** (diagnostics, progress,
   log) with no `id`; the client pumps and ignores/handles them without blocking request/response
   correlation. A background reader thread + a `{id: future}` map is the clean approach.
6. **Shutdown** — `shutdown` then `exit`; kill the process tree on teardown.

**Gotchas to bake in from day one:**
- **Positions are 0-indexed**, and LSP character offsets are **UTF-16** by default — converting
  our (1-indexed, UTF-8) world to/from LSP is the classic source of off-by-one/emoji bugs.
- Servers need a **warmup/indexing** period after `initialize`; `workspace/symbol` may be empty
  until indexing completes — retry/backoff briefly.
- `workspace/symbol` quality **varies by server**; some need a non-empty query and don't do fuzzy.

## 5. Server lifecycle & language coverage

- **Detect language** from file extension / project markers, map to a server command.
- **Spawn lazily, keep alive** for the session (servers are slow to start + index), cache by
  language, one per language. Shut down at exit.

| Language | Server (local process) | Install (one-time, like ripgrep) |
|---|---|---|
| Python | `pyright-langserver --stdio` or `pylsp` | `npm i -g pyright` / `pip install python-lsp-server` |
| TypeScript/JS | `typescript-language-server --stdio` | `npm i -g typescript-language-server typescript` |
| Go | `gopls` | `go install golang.org/x/tools/gopls@latest` |
| Rust | `rust-analyzer` | rustup component |
| C/C++ | `clangd` | distro package |

**Graceful fallback (keep the current behavior):** if no server is installed for the language,
the `lsp` tool returns a clear "no language server for `.py`; falling back — use `grep`" message
and the model uses `grep`/`read` as today. Same pattern as ripgrep → Python-grep fallback. This
also means the agent still works with **zero** extra installs; LSP is an *enhancement*.

## 6. Integration with the existing architecture

```
agent/
  lsp/
    __init__.py
    client.py        # JSON-RPC over stdio: framing, request/response, notification pump
    servers.py       # language → server command; detection; lifecycle cache
  tools/
    lsp_tool.py      # the `lsp` tool: op -> LSP request(s) -> path:line results
```
- **Register** `lsp` in `default_registry()` (available when a server is detected; otherwise the
  tool advertises fallback).
- **Loop instructions**: add a line — *"Prefer `lsp` to resolve a symbol precisely (defs/refs/
  type); use `grep` only for free-text search or when no language server is available. After
  `lsp` gives a location, `read` those lines to ground your answer."*
- **Sandbox**: `rootUri` = `sandbox.root`; every returned path is relativized + contained, same
  as the other tools. The server only ever sees the project directory.
- **Offline**: servers run locally; only *installing* them needs the network once (documented
  alongside the ripgrep / vocab setup). No runtime network.

## 7. Milestones

| M | Deliverable |
|---|---|
| **M1** | JSON-RPC stdio client (`client.py`): framing, `initialize`/`initialized` handshake, notification pump, `shutdown`/`exit`. Spawn pyright, complete handshake. |
| **M2** | `defs` via `workspace/symbol` (name → definition location). The core bridge. |
| **M3** | `refs` (`references`) + `info` (`hover`, formatted signature/type/doc). |
| **M4** | `outline` (`documentSymbol`); language detection + server cache + graceful fallback. |
| **M5** | Loop-instruction integration; wire into the funnel; add to the eval harness (does LSP raise groundedness / cut wrong-symbol answers vs grep-only?). |

## 8. Testing (a big win: testable offline on the build machine)

Unlike model-dependent code, the **LSP client needs no gpt-oss** — it talks to a language server,
which is fully local. So M1–M4 can be built *and* tested on the build machine:

- Install a local server here (`pip install python-lsp-server` or `npm i -g pyright`) and run the
  JSON-RPC client against this very repo: assert `defs("run_turn")` → `agent/loop.py:<line>`,
  `refs("budget")` includes `loop.py`, `info("Result")` returns the dataclass signature,
  `outline("agent/loop.py")` lists the functions. Real server, real repo, no model, no network.
- Test **framing** (Content-Length parsing) and **UTF-16 ↔ UTF-8 position** conversion with unit
  cases (multibyte lines).
- Test **fallback**: no server for `.xyz` → clear message, no crash.
- Then the full agent (model + LSP tool) runs on the GPU box as usual.

## 9. Risks & mitigations

- **UTF-16 offsets** → central conversion helpers + multibyte unit tests.
- **Startup/indexing latency** → spawn once per session, keep alive; brief retry on empty
  `workspace/symbol` during indexing; a hard timeout so a hung server can't block a turn.
- **Per-language install** → auto-detect + graceful grep fallback; document installs.
- **`workspace/symbol` variance** → when it returns nothing, fall back to `grep 'def name|class
  name'` + `textDocument/definition` at that hit.
- **JSON-RPC robustness** → background reader thread, `{id: future}` correlation, ignore unknown
  notifications; never let server chatter deadlock a request.
- **Monorepo indexing cost** → scope `rootUri` to `--project`; document that first query on a huge
  repo may lag while indexing.

## 10. Out of scope (for now)

Live diagnostics streaming, code actions / rename / formatting (those belong with the future
**edit tier**), multi-root workspaces, and semantic tokens/highlighting. This plan is read-only
intelligence that feeds the existing navigate-and-read loop.
