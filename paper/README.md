# ICITIIT 2027 submission

**Title:** Who Writes the Control Plane? Control-Token Provenance in a Fully
Offline Code Agent

**Venue:** ICITIIT 2027, IIIT Kottayam, 19–20 Feb 2027.
Track: *Secure, Trustworthy, Privacy-Preserving, and Quantum Computing
Technologies.* Accepted and presented papers are submitted for possible
publication in IEEE Xplore.

**Submission:** Microsoft CMT3 — <https://cmt3.research.microsoft.com/ICITIIT2027>

**Deadline:** **20 October 2026, 11:59 PM IST.** Submit on the 19th.

---

## Files

| Path | What it is |
|---|---|
| `main.tex` | The manuscript. IEEEtran conference class |
| `references.bib` | Bibliography, separate from the manuscript |
| `figures/fig1-architecture.tex` | Fig. 1 — system architecture (double column) |
| `figures/fig2-control-plane.tex` | Fig. 2 — hybrid control-plane anatomy (single column) |
| `figures/fig3-dispatch-path.tex` | Fig. 3 — attack path and where the gate cuts it (single column) |

All three figures are **TikZ source**, not images. They compile on Overleaf
with no binary assets, and they are editable as text. Each is wrapped in
`\resizebox` so it cannot overrun its column regardless of font metrics.

## Building

Upload the whole `paper/` directory to Overleaf and set `main.tex` as the root
document. No packages beyond a standard TeX Live installation are required:
`IEEEtran`, `cite`, `amsmath`, `amssymb`, `graphicx`, `xcolor`, `booktabs`,
`url`, `tikz`, `hyperref`.

Locally, once a TeX distribution is installed:

```bash
latexmk -pdf -interaction=nonstopmode main.tex
```

There is no TeX toolchain on the development machine, so **this draft has not
been compiled.** Compile it first and fix any TikZ complaints before writing
further prose.

## Placeholder discipline

Every unmeasured quantity is wrapped in `\PH{...}` and renders **red and bold**.

- A placeholder may be removed **only** by substituting a value that exists in
  a committed result file. Never by estimating, and never by copying a number
  from a related paper.
- Before submitting, open the compiled PDF and search for red text. If any
  remains, the paper is not ready.

## Outstanding `TODO` items

Grep the sources for `TODO(` . At the time of writing:

| Marker | Action |
|---|---|
| `TODO(venue)` in `main.tex` | **Confirm the page limit.** The draft is written for 6 pages. If smaller, cut Table III (`tab:renderer`) first, then §IX |
| `TODO(venue)` in `main.tex` | **Confirm whether review is double-blind.** Not stated on the CFP page. If it is, switch to the anonymous `\author` block and remove the artifact statement |
| `TODO(diagram)` in `main.tex` | Optional inset on Fig. 1 showing the permission modes, if the page budget allows |

## Citation hygiene

`references.bib` marks every entry:

- `[READ]` — abstract or full text fetched and read on 2026-10-06.
- `[TITLE]` — title, ID and venue confirmed from search results only, **not
  read**. Several carry numbers taken from search snippets; those are *not*
  evidence.

**Before submission, every `[TITLE]` entry that is actually cited must be
fetched, read, and promoted to `[READ]`, or removed.** Three entries are
load-bearing and must be read in full regardless:

1. `zhan2026samebytes` — the paper this work answers.
2. `yang2026nameless` — the complementary defense; the positioning in §VII
   depends on what it does and does not cover.
3. `usama2026controltoken` — the closest attack paper. Only its abstract has
   been read. If it turns out to cover Harmony specifically, §VII must be
   rewritten and the novelty claim narrowed.

`harness2026` (83 pp, eleven coding agents) should be read before the claim in
§III-C that tolerant parsing and prose-level recovery are characteristic of
deployed harnesses.

## Novelty position, as established on 2026-10-06

What exists, and why it does not take the claim:

| Work | Why it does not collide |
|---|---|
| Same Bytes, Different Authority (2609.35932) | Evaluates Qwen3, Llama-3.1, GLM-4.5, Seed-OSS. Never gpt-oss, never Harmony. Names tool-protocol tokens as the gap it leaves |
| Nameless Tokenization (2609.16984) | Tokenizer-level. Protects reserved identifiers; a textual control field has no identifier to protect |
| Control-Token Injection (2609.27542) | Attack paper, format-agnostic, targets reasoning oversight rather than tool selection; defense is sanitisation |
| AGATE (2609.30830) | Gates on **parsed structured calls**; never mentions tokenizers, control tokens, or chat templates |
| A Deterministic Control Plane for LLM Coding Agents (2606.26924) | "Control plane" in the orchestration sense. Terminology collision only — hence the title wording |

Field evidence that the failure mode is real in deployed systems (defect
reports, cited as motivation and never as research results): llama.cpp #27720,
vLLM #58825, unsloth #5162.

**Three directly adjacent papers appeared in September 2026 alone.** Re-run the
novelty check immediately before submission.
