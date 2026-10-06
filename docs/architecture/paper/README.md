# Paper figures — architecture of the proposed system

Three figures, one idea each. Print-safe: no colour, no gradients, no shadows; greyscale
fills differentiated by label, not hue. Sized for a two-column template — Fig. 1 at column
width, Figs. 2–3 full width.

| File | Role in the paper | Width |
|---|---|---|
| `fig1-system.svg` | Section 3 opener — the static architecture (C1) | one column |
| `fig2-cscd.svg` | **Section 4, the hero figure** — channel-scoped constrained decoding (C2) | two columns |
| `fig3-dispatch-surface.svg` | Section 5 — the dispatch surface and what closes it (C3) | two columns |

If a reviewer reads only one figure, it must be Fig. 2. Everything else is context.

---

## Figure 1 — static architecture

**One idea: the whole system is one host, and we own the token stream.**

The stack splits once, below the turn state machine, into a *model path* and a *tool path*:

- **Model path** — decoding strategy → Harmony codec → inference client → `llama-server`.
  The codec renders the conversation to token IDs and parses token IDs back into channels, so
  no chat template ever intervenes. This is what makes Fig. 2 possible at all, and it is the
  one architectural decision the method depends on.
- **Tool path** — tool registry → permission engine → capability profile → working tree.
  Three tiers (navigate, execute, write), each gated by the engine and then confined by the
  profile. The profile is the part that is new: it puts `bash` behind the same wall as the
  write tools.
- **Instrumentation** spans both paths. Everything the agent decides or observes lands in the
  ledger and the JSONL trace, which is where every number in the paper comes from.

The dashed outer boundary is the claim the framing rests on: one host, no cloud service, no
egress. Keep that boundary in the figure — it is the privacy argument in one line.

> **Caption.** *Architecture of the proposed agent. The turn state machine drives a model path
> (client-side Harmony rendering over raw token IDs) and a tool path (three tool tiers behind
> the permission engine and an OS-enforced capability profile). Shaded components are
> introduced in this work. All components execute on a single host with no network egress.*

## Figure 2 — channel-scoped constrained decoding

**One idea: the constraint never touches the reasoning.**

Read the figure left to right as one assistant completion. The lower band is the whole
argument: the grammar is inactive across the reasoning region and active only from the moment
the model begins a tool call. The dark segment is the header the orchestrator writes itself —
the model never emits it, so a malformed header is not unlikely, it is unreachable.

The three numbered callouts are the three claims, and each maps to one metric in the
evaluation: ① reasoning-quality delta, ② tool-call rate per task (the suppression detector),
③ schema-violation rate.

> **Caption.** *Channel-scoped constrained decoding. Phase A decodes unconstrained; on
> detecting a commentary-channel onset, Phase B selects a recipient from the registry, the
> canonical header is emitted by the orchestrator (dark), and Phase C decodes the argument body
> under that tool's JSON schema. The grammar is inactive over the reasoning channel, so the
> constraint can neither tax reasoning nor suppress invocation.*

## Figure 3 — dispatch surface

**One idea: the attack path exists in (a) and is broken twice in (b).**

Both panels are the same six-stage chain with identical geometry, so the eye compares arrows
rather than layouts. Panel (a) draws the path heavy and unbroken: untrusted repository content
reaches the dispatcher because the agent restates what it reads, and reaches execution because
`bash` is ungated. Panel (b) breaks it at two independent points — one from the method
(structurally valid calls remove the need for prose-level dispatch) and one from enforcement
(the capability profile).

Two independent breaks is deliberate and worth saying in the text: the security claim does not
rest on the decoding contribution alone.

> **Caption.** *The dispatch surface. (a) With tolerant parsing and prose-level call recovery,
> untrusted repository content transitively reaches the tool dispatcher and then an ungated
> shell. (b) Channel-scoped constrained decoding removes the need for prose-level dispatch, and
> the capability profile confines execution; the path is broken at two independent points.*

---

## Dropping these into LaTeX

Convert once (keeps text as vectors, so it stays crisp and selectable):

```bash
for f in fig1-system fig2-cscd fig3-dispatch-surface; do inkscape "$f.svg" --export-type=pdf --export-filename="$f.pdf"; done
```

Then, for a two-column template:

```latex
\begin{figure}[t]
  \centering
  \includegraphics[width=\columnwidth]{figures/fig1-system.pdf}
  \caption{Architecture of the proposed agent. \ldots}
  \label{fig:system}
\end{figure}

\begin{figure*}[t]
  \centering
  \includegraphics[width=\textwidth]{figures/fig2-cscd.pdf}
  \caption{Channel-scoped constrained decoding. \ldots}
  \label{fig:cscd}
\end{figure*}
```

Figures 2 and 3 use `figure*` (full width). Figure 1 uses `figure`.

---

## Section 3 prose skeleton

Order the architecture section so each paragraph earns the next figure. Author the sentences
yourself; this is the claim order, not the text.

1. **Deployment setting.** One host, one GPU, no cloud service, no egress. State what is
   trusted (the user, their shell) and what is not (**repository contents, command output,
   dependency metadata, filenames**). Name the `AGENT_BASE_URL` plain-HTTP caveat here rather
   than letting a reviewer find it — scoping a limitation yourself is strength, not weakness.
2. **Why client-side Harmony.** Rendering and parsing token IDs ourselves is presented as an
   enabling decision, not a quirk: it is the only reason a mid-completion grammar hand-off is
   available. Forward-reference Fig. 2 explicitly.
3. **The turn.** State machine plus recovery policies; note that an experimental arm is a list
   of enabled policies, which is what makes the ablation in Section 6 a configuration rather
   than a code fork.
4. **Tool tiers and the funnel.** navigate → execute → write, retrieval-free by design (no
   index, no embedding model). Keep this short — it is background now, not a contribution.
5. **Governance.** Permission engine, then capability profile. Be explicit that the engine is
   policy and the profile is enforcement, and that the profile is what brings `bash` inside the
   boundary.
6. **Instrumentation.** Ledger, trace, manifest, seed. One sentence that every reported number
   derives from a committed trace file.

A thing to avoid: do not describe the recovery heuristics here as features. In this paper they
are the *object of study* (Section 5), and introducing them as architecture undercuts the
finding.
