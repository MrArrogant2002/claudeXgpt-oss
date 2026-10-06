# ICITIIT 2027 submission — draft for supervisor review

**Venue.** 8th International Conference on Innovative Trends in Information Technology
(ICITIIT 2027), IIIT Kottayam, 19–20 February 2027. Technically co-sponsored by IEEE Kerala
Section; accepted papers go to IEEE Xplore.
**Track.** Secure, Trustworthy, Privacy-Preserving, and Quantum Computing Technologies.
**Theme fit.** "Beyond Generative AI: Engineering Intelligent, Trustworthy, Autonomous, and
Responsible Computing Systems" — the paper is about making an autonomous agent trustworthy and
contained, which is the theme almost verbatim.

| | |
|---|---|
| Submission deadline | **20 October 2026** |
| Notification | 20 December 2026 |
| Camera-ready | 10 January 2027 |
| Page limit | **6 pages, hard** |
| Template | IEEEtran, `conference` option (set in `main.tex`) |
| Portal | Microsoft CMT — `cmt3.research.microsoft.com/ICITIIT2027` |

## Status

**This is a draft for project confirmation, not a submittable paper.** Every quantity is a
visible red placeholder rendered by the `\PH{}` macro. Nothing has been measured. The red
styling is deliberate: a placeholder cannot survive to camera-ready unnoticed, and no number
should ever be substituted except from a committed trace file.

## Files

```
main.tex              IEEEtran manuscript, 6-page target
references.bib        bibliography — SEE THE WARNING BELOW
figures/fig1-system.png    architecture
figures/fig2-cscd.png      channel-scoped constrained decoding (full width)
figures/fig3-dispatch.png  dispatch surface, before and after
```

## Building

No LaTeX toolchain is installed on the development laptop, so **this document has not been
compiled or page-counted.** Build it on Overleaf (upload the `paper/` folder) or on the GPU box:

```bash
cd paper && pdflatex main && bibtex main && pdflatex main && pdflatex main
```

Expect to fix small things on the first build. The most likely issues: the page count
overruns six (see below), and `fig2-cscd.png` at `0.92\textwidth` may need adjusting.

## Before anything else: verify the bibliography

`references.bib` has two tiers. The `[VERIFIED]` entries are standard works. The
`[UNVERIFIED]` entries were located by literature search on 2026-09-30 and their author lists
are the literal string `[AUTHORS TO VERIFY]`. **Open each PDF, confirm the title, authors, year
and venue, and correct the entry.** Three of them carry real argumentative weight and must be
read in full, not just cited:

1. `repairnot2026` — closest prior work.
2. `constrainttax2026` — the objection the method answers. If this paper's findings differ from
   our characterisation, Section IV-C must change.
3. `structfail2026` — grounds the semantic-gap claim.

If a search result turns out not to exist, delete the entry and the sentence that cites it.
Citing a work whose details are wrong is an integrity failure, not a typo.

## Page budget

| Section | Target |
|---|---|
| I Introduction | 0.75 |
| II Related Work | 0.6 |
| III System Architecture + Fig. 1 | 1.0 |
| IV Channel-Scoped Constrained Decoding + Fig. 2 (full width) | 1.4 |
| V Dispatch Surface + Fig. 3 | 0.9 |
| VI Evaluation Design | 0.6 |
| VII Results (2 tables) | 0.4 |
| VIII Threats + IX Conclusion | 0.35 |
| References | 0.5 |

That totals slightly over six. **If it overruns, cut in this order:** the acknowledgement
section, Table I (fold the taxonomy into prose), then Related Work paragraph four (context
management), then the formal equations in Section V-A (state the three surfaces in a sentence).
Do not cut Fig. 2 or shrink it below `0.9\textwidth` — it is the paper.

## What a reviewer will attack, and where it is handled

| Objection | Where answered |
|---|---|
| "Why not just use constrained decoding?" | That *is* the method. Sec. IV |
| "Constraints suppress tool calls in open-weight models" | Sec. IV-C, second paragraph — suppression is structurally impossible here |
| "Constraints hurt reasoning" | Sec. IV-C, first paragraph + the reasoning-quality-delta metric |
| "Is prose-level dispatch a real system or a straw man?" | It is standard practice in shipped agents, and it was in **our own** system before this work. Say so plainly in the rebuttal |
| "Your sandbox has a TOCTOU race" | Acknowledged via `balkan2026`; the capability profile is the mitigation |
| "One model, few repositories" | Sec. VIII, External |

## Honest gaps the supervisor should see

1. **No results exist.** The build plan
   ([`../docs/plans/conference-paper-build-plan.md`](../docs/plans/conference-paper-build-plan.md))
   estimates 14 weeks of work. The deadline is **15 days** away. This is the central thing to
   discuss: either the submission targets a later venue, or the scope is cut to something
   measurable in two weeks — most plausibly Section IV alone (decoding arms on the repository-QA
   suite), dropping Section V to future work.
2. **The CSCD feasibility spike has not been run.** The method assumes the local server accepts
   `grammar`/`json_schema` alongside a token-ID prompt with prefix caching intact. This is
   one day of work and it gates everything. See §2.4 of the build plan.
3. **The capability profile does not exist in code.** Figure 1 shows the proposed system. That
   is correct for a paper, but it is not the system as it stands today.
4. **Prose authorship.** The text is a structural scaffold — claim order and argument shape.
   It must be rewritten in the author's own words before submission.
