"""Generate the paper's figures as images.

Emits a vector PDF (what pdflatex should include) and a PNG (for viewing,
slides, and review) for each figure. Run from the repository root:

    python paper/figures/make_figures.py

Needs matplotlib only. On this machine matplotlib lives in the system Python,
not the project venv, because the agent's runtime dependency surface is
deliberately `openai-harmony` and `requests` and nothing else.

Why a script rather than hand-drawn assets: the figures are then reproducible,
reviewable as a diff, and cannot drift from the architecture they describe. The
original TikZ sources are kept under `tikz/` for reference.

Conventions, held across all three figures:
    solid box            an ordinary component
    grey fill            contributed by this work
    dashed outline       attacker-controlled, or a decision point
    dotted outline       annotation
    heavy dashed arrow   flow of untrusted bytes
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

# --- house style ------------------------------------------------------------
GREY = "#d9d9d9"
LIGHT = "#f2f2f2"
EDGE = "#000000"
FS = 6.2          # body text inside boxes
FS_SMALL = 5.3    # sub-labels
FS_TINY = 4.8     # edge labels and annotations

plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "pdf.fonttype": 42,  # embed TrueType, not Type 3: required by many venues
        "ps.fonttype": 42,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.02,
    }
)


def canvas(w_in: float, h_in: float):
    fig, ax = plt.subplots(figsize=(w_in, h_in))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")
    # Data units per typographic point on the vertical axis. Line spacing has
    # to be computed in points and drawn in data units, and the conversion
    # depends on the figure height, so it is stashed per-axes rather than
    # guessed with a constant.
    ax._upp = 100.0 / (h_in * 72.0)
    return fig, ax


def box(
    ax,
    x: float,
    y: float,
    w: float,
    h: float,
    lines: list[tuple[str, float, str]],
    *,
    fill: str = "white",
    style: str = "solid",
    lw: float = 0.8,
) -> tuple[float, float]:
    """Draw a rounded box centred at (x, y); `lines` is [(text, size, weight)].

    Returns the centre, so callers can chain arrows without recomputing it.
    """
    dash = {"solid": None, "dashed": (0, (3, 2)), "dotted": (0, (1, 1.6))}[style]
    patch = FancyBboxPatch(
        (x - w / 2, y - h / 2),
        w,
        h,
        boxstyle="round,pad=0,rounding_size=1.2",
        linewidth=lw,
        edgecolor=EDGE,
        facecolor=fill,
        linestyle=dash if dash else "solid",
        mutation_aspect=1,
        zorder=3,
    )
    ax.add_patch(patch)
    upp = getattr(ax, "_upp", 0.45)
    leading = [size * 1.45 * upp for _, size, _ in lines]  # 1.45 em line spacing
    cursor = y + sum(leading) / 2
    for (text, size, emphasis), lead in zip(lines, leading):
        # `emphasis` is one of normal | bold | italic. matplotlib splits these
        # across two keywords, so route italic to `style` and the rest to
        # `fontweight`; passing "italic" as a weight raises.
        style = "italic" if emphasis == "italic" else "normal"
        weight = emphasis if emphasis in ("normal", "bold") else "normal"
        cursor -= lead / 2
        ax.text(
            x, cursor, text, ha="center", va="center",
            fontsize=size, fontweight=weight, style=style, zorder=4,
        )
        cursor -= lead / 2
    return x, y


def arrow(ax, p0, p1, *, text=None, style="solid", lw=0.75, text_pos=0.5, dy=2.2,
          connect="arc3,rad=0"):
    dash = {"solid": None, "dashed": (0, (3.5, 2)), "dotted": (0, (1, 1.6))}[style]
    ax.add_patch(
        FancyArrowPatch(
            p0, p1,
            arrowstyle="-|>,head_length=2.4,head_width=1.5",
            mutation_scale=1,
            linewidth=lw,
            linestyle=dash if dash else "solid",
            color=EDGE,
            connectionstyle=connect,
            shrinkA=0, shrinkB=0,
            zorder=2,
        )
    )
    if text:
        mx = p0[0] + (p1[0] - p0[0]) * text_pos
        my = p0[1] + (p1[1] - p0[1]) * text_pos + dy
        ax.text(mx, my, text, ha="center", va="center", fontsize=FS_TINY,
                style="italic", zorder=5,
                bbox=dict(boxstyle="square,pad=0.12", fc="white", ec="none"))


def cross(ax, x: float, y: float, r: float = 1.6, label: str | None = None):
    """A drawn cut. Drawn rather than symbolic so it survives greyscale print."""
    ax.plot([x - r, x + r], [y - r, y + r], color=EDGE, lw=1.1, zorder=6)
    ax.plot([x - r, x + r], [y + r, y - r], color=EDGE, lw=1.1, zorder=6)
    if label:
        ax.text(x + r + 1.5, y, label, ha="left", va="center",
                fontsize=FS_TINY, style="italic", zorder=6)


def save(fig, out_dir: Path, stem: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        path = out_dir / f"{stem}.{ext}"
        fig.savefig(path, dpi=400 if ext == "png" else None)
        print(f"  wrote {path}")
    plt.close(fig)


# ===========================================================================
# Fig. 1 -- system architecture
#
# The point: one token stream, two consumers, deliberately not one
# implementation. Tolerant parsing feeds history; the gate feeds dispatch.
# ===========================================================================
def fig1(out_dir: Path) -> None:
    """Vertical budget is explicit, because the boxes and the routed arrows have
    to be placed against each other by hand and silent collisions are the main
    failure mode of a generated diagram."""
    fig, ax = canvas(7.16, 3.6)

    Y_REJECT, Y_LOOP, Y_PARSE = 93.5, 87.0, 79.0
    Y_MODEL, Y_GATE = 66.0, 56.0
    Y_DISP, Y_READ, Y_EXEC, Y_ENF, Y_REPO = 41.0, 31.0, 23.0, 13.0, 5.0

    box(ax, 13, Y_MODEL, 23, 12, [
        ("llama.cpp /completion", FS, "normal"),
        ("gpt-oss-20b, MXFP4", FS_SMALL, "normal"),
        ("no chat template", FS_SMALL, "italic"),
    ])
    box(ax, 42, Y_MODEL, 23, 12, [
        ("Harmony codec", FS, "bold"),
        ("renders to, and returns,", FS_SMALL, "normal"),
        ("raw token identifiers", FS_SMALL, "bold"),
    ])
    box(ax, 30, Y_LOOP, 28, 7.5, [
        ("Turn loop", FS, "bold"),
        ("render → infer → admit → dispatch", FS_SMALL, "normal"),
    ])

    arrow(ax, (24.5, Y_MODEL + 3), (30.5, Y_MODEL + 3), text="prefill ids")
    arrow(ax, (30.5, Y_MODEL - 3), (24.5, Y_MODEL - 3), text="output ids", dy=-3.2)
    arrow(ax, (38, Y_MODEL + 6), (33, Y_LOOP - 3.75))

    # one stream, two consumers
    fx = 58
    ax.plot([53.5, fx], [Y_MODEL, Y_MODEL], color=EDGE, lw=0.75, zorder=2)
    ax.text(fx - 2, Y_MODEL + 3.2, "one stream", ha="center",
            fontsize=FS_TINY, style="italic")
    ax.plot([fx, fx], [Y_GATE, Y_PARSE], color=EDGE, lw=0.75, zorder=2)

    box(ax, 78, Y_PARSE, 28, 11, [
        ("tolerant parse", FS, "bold"),
        ("salvages malformed headers", FS_SMALL, "normal"),
        ("→ history, display", FS_SMALL, "italic"),
    ])
    box(ax, 78, Y_GATE, 31, 12, [
        ("provenance gate", FS, "bold"),
        ("reserved ids · canonical order", FS_SMALL, "normal"),
        ("this turn only · exact registry", FS_SMALL, "normal"),
    ], fill=GREY, lw=1.15)

    arrow(ax, (fx, Y_PARSE), (64, Y_PARSE))
    arrow(ax, (fx, Y_GATE), (62.5, Y_GATE))

    # rejection returns to the loop along the top
    ax.plot([93.5, 96.5], [Y_GATE, Y_GATE], color=EDGE, lw=0.8, ls=(0, (3.5, 2)), zorder=2)
    ax.plot([96.5, 96.5], [Y_GATE, Y_REJECT], color=EDGE, lw=0.8, ls=(0, (3.5, 2)), zorder=2)
    arrow(ax, (96.5, Y_REJECT), (30, Y_REJECT), style="dashed")
    ax.text(63, Y_REJECT + 2.2, "rejected → returned to the model as data",
            ha="center", fontsize=FS_TINY, style="italic")
    ax.plot([30, 30], [Y_LOOP + 3.75, Y_REJECT], color=EDGE, lw=0.8,
            ls=(0, (3.5, 2)), zorder=2)

    box(ax, 78, Y_DISP, 22, 7, [("tool dispatcher", FS, "normal")])
    arrow(ax, (78, Y_GATE - 6), (78, Y_DISP + 3.5))
    ax.text(79.5, (Y_GATE + Y_DISP) / 2 - 1.5, "admitted calls only", ha="left",
            fontsize=FS_TINY, style="italic")

    box(ax, 78, Y_READ, 31, 6.5, [("read tier", FS_SMALL, "bold"),
                                  ("list_dir  glob  grep  read", FS_TINY, "normal")])
    box(ax, 78, Y_EXEC, 31, 5.5, [("exec tier   bash", FS_SMALL, "bold")])
    box(ax, 40, Y_DISP, 24, 6.5, [("write tier", FS_SMALL, "bold"),
                                  ("edit  write  multi_edit", FS_TINY, "normal")])
    arrow(ax, (78, Y_DISP - 3.5), (78, Y_READ + 3.25))
    arrow(ax, (78, Y_READ - 3.25), (78, Y_EXEC + 2.75))
    arrow(ax, (67, Y_DISP), (52.2, Y_DISP))

    box(ax, 55, Y_ENF, 68, 6, [
        ("path sandbox  ·  permission engine (5 modes, fail-closed)"
         "  ·  bubblewrap", FS_SMALL, "normal")])
    arrow(ax, (78, Y_EXEC - 2.75), (78, Y_ENF + 3))

    box(ax, 55, Y_REPO, 68, 7, [
        ("repository — files, filenames, dependency metadata, build output",
         FS_SMALL, "bold"),
        ("attacker-controlled", FS_TINY, "italic"),
    ], fill=LIGHT, style="dashed")
    arrow(ax, (38, Y_REPO + 3.5), (38, Y_ENF - 3), style="dashed", lw=1.3)
    ax.text(33, (Y_REPO + Y_ENF) / 2, "untrusted bytes", ha="right",
            fontsize=FS_TINY, style="italic")

    # untrusted results re-enter upstream of the gate: the reason the gate must
    # sit on identifiers rather than on text
    ax.plot([21, 21], [Y_ENF, Y_MODEL - 10], color=EDGE, lw=1.3,
            ls=(0, (3.5, 2)), zorder=1)
    arrow(ax, (21, Y_MODEL - 10), (33, Y_MODEL - 6), style="dashed", lw=1.3)
    ax.text(22.5, (Y_ENF + Y_MODEL) / 2 - 4, "tool results\n(untrusted)",
            ha="left", va="center", fontsize=FS_TINY, style="italic")

    box(ax, 50, 50, 99, 98, [], style="dashed", fill="none", lw=0.7)
    ax.text(50, 99.6, "one host  ·  no network egress  ·  "
            "no index, no embedding model",
            ha="center", va="bottom", fontsize=FS_SMALL, style="italic")

    save(fig, out_dir, "fig1-architecture")


# ===========================================================================
# Fig. 2 -- the hybrid control plane
#
# The point: four fields are reserved identifiers and three are ordinary text,
# and the recipient -- the field naming the tool -- is one of the three.
# Token counts are the measured values from scripts/probe_control_tokens.py.
# ===========================================================================
def fig2(out_dir: Path) -> None:
    fig, ax = canvas(3.5, 2.55)

    H = 9.5          # cell height
    GAP = 2.0
    LEFT = 3.0
    Y1, Y2 = 84.0, 58.0

    # (field, class, measured token count, width). Widths are chosen so each
    # row ends inside the canvas; a row that overflows is the easiest way for a
    # generated figure to go wrong unnoticed, so the totals are asserted below.
    row1 = [("<|channel|>", "R", 1, 24), ("commentary", "T", 2, 22),
            ("to=functions.read", "T", 4, 33)]
    row2 = [("<|constrain|>", "R", 1, 25), ("json", "T", 1, 11),
            ("<|message|>", "R", 1, 23), ('{"path":"a.py"}', "D", 6, 26)]
    for row in (row1, row2):
        width = sum(w for *_, w in row) + GAP * (len(row) - 1)
        assert LEFT + width <= 97, f"row overflows the canvas: {LEFT + width:.1f}"

    centres = {}

    def draw_row(cells, y):
        x = LEFT
        for label, cls, n, w in cells:
            fill = {"R": GREY, "T": "white", "D": LIGHT}[cls]
            style = {"R": "solid", "T": "dashed", "D": "dotted"}[cls]
            lw = 1.15 if cls == "R" else 0.85
            box(ax, x + w / 2, y, w, H, [(label, FS_SMALL, "normal")],
                fill=fill, style=style, lw=lw)
            ax.text(x + w / 2, y - H / 2 - 4.5, f"{cls} ({n})", ha="center",
                    va="center", fontsize=FS_TINY, fontweight="bold")
            centres[label] = (x + w / 2, y)
            x += w + GAP

    draw_row(row1, Y1)
    draw_row(row2, Y2)

    # The recipient is what selects the tool, and it is class T.
    rx, ry = centres["to=functions.read"]
    ax.annotate(
        "selects which tool executes —\nand has no token identity to verify",
        xy=(rx, ry + H / 2), xytext=(rx - 6, 99), fontsize=FS_TINY, style="italic",
        ha="center", va="top",
        arrowprops=dict(arrowstyle="-|>", lw=0.8, color=EDGE, shrinkA=1, shrinkB=1),
    )

    legend = [
        ("R", GREY, "solid", "reserved control token — forgery is detectable"),
        ("T", "white", "dashed", "plain-text control field — no identity exists"),
        ("D", LIGHT, "dotted", "argument data"),
    ]
    y = 34
    for tag, fill, style, text in legend:
        box(ax, 5.5, y, 5, 4.5, [], fill=fill, style=style,
            lw=1.15 if tag == "R" else 0.85)
        ax.text(10.5, y, f"{tag}  {text}", ha="left", va="center", fontsize=FS_TINY)
        y -= 9.5

    ax.text(3, 2, "(n) = tokens, measured against the released vocabulary",
            ha="left", va="bottom", fontsize=FS_TINY, style="italic", color="#444444")

    save(fig, out_dir, "fig2-control-plane")


# ===========================================================================
# Fig. 3 -- the dispatch path and the two independent cuts
# ===========================================================================
def fig3(out_dir: Path) -> None:
    """Two independent cuts, not one: the renderer decides whether the span ever
    acquires reserved identifiers, and the gate decides whether a span lacking
    them can become a call. Either alone breaks the chain."""
    fig, ax = canvas(3.5, 5.0)

    X, W = 48, 84
    Y = dict(s1=96, s2=87, c1=77.5, s3=63, s4=52.5, s5=44, c2=33.5, s6=19, cats=7)

    box(ax, X, Y["s1"], W, 8.5, [
        ("1   A repository file contains a header-shaped span", FS_SMALL, "normal"),
        ("...<|channel|>commentary to=functions.bash...", FS_TINY, "normal"),
    ], fill=LIGHT, style="dashed")
    box(ax, X, Y["s2"], W, 6, [
        ("2   read or grep returns it as a tool result", FS_SMALL, "normal"),
    ], fill=LIGHT, style="dashed")
    arrow(ax, (X, Y["s1"] - 4.25), (X, Y["s2"] + 3))

    box(ax, X, Y["c1"], W, 9.5, [
        ("Renderer admission policy   (C1)", FS_SMALL, "bold"),
        ("do the span's markers become reserved identifiers?", FS_TINY, "normal"),
    ], fill=GREY, lw=1.15)
    arrow(ax, (X, Y["s2"] - 3), (X, Y["c1"] + 4.75))

    box(ax, X, Y["s3"], W, 9, [
        ("3   The span enters the context — as ordinary", FS_SMALL, "normal"),
        ("subwords, byte-identical to the real thing", FS_TINY, "normal"),
    ])
    box(ax, X, Y["s4"], W, 9, [
        ("4   The model restates it while reasoning", FS_SMALL, "normal"),
        ("(ordinary behaviour when summarising a file)", FS_TINY, "normal"),
    ])
    box(ax, X, Y["s5"], W, 6, [
        ("5   A header-shaped span appears in the output", FS_SMALL, "normal"),
    ])
    arrow(ax, (X, Y["s3"] - 4.5), (X, Y["s4"] + 4.5))
    arrow(ax, (X, Y["s4"] - 4.5), (X, Y["s5"] + 3))

    box(ax, X, Y["c2"], W, 9.5, [
        ("Provenance gate   (C3)", FS_SMALL, "bold"),
        ("reserved ids · canonical order · this turn · exact registry",
         FS_TINY, "normal"),
    ], fill=GREY, lw=1.15)
    arrow(ax, (X, Y["s5"] - 3), (X, Y["c2"] + 4.75))

    box(ax, X, Y["s6"], W, 6, [
        ("6   bash executes the attacker's command", FS_SMALL, "normal"),
    ])
    arrow(ax, (X, Y["c2"] - 4.75), (X, Y["s6"] + 3))

    cross(ax, X, (Y["c1"] - 4.75 + Y["s3"] + 4.5) / 2, r=1.9,
          label="escaped as subwords\n(measured: 0 of 7)")
    cross(ax, X, (Y["c2"] - 4.75 + Y["s6"] + 3) / 2, r=1.9,
          label="no reserved-id header,\nso not a call")

    # What the gate reports instead of a repaired call. These are the
    # measurement grain, so they belong in the figure rather than only in prose.
    cat = 4.0  # these lines are long; sized to stay inside the box
    box(ax, X, Y["cats"], W, 12, [
        ("What the gate reports instead", FS_TINY, "bold"),
        ("reserved_token_in_body · non_canonical_header", cat, "normal"),
        ("duplicate_recipient · malformed_recipient · unknown_tool", cat, "normal"),
        ("unknown_channel · bad_constraint_type", cat, "normal"),
        ("missing_call_terminator · invalid_json_arguments", cat, "normal"),
    ], style="dotted")
    ax.plot([X + W / 2, 95], [Y["c2"], Y["c2"]], color=EDGE, lw=0.6,
            ls=(0, (1, 1.6)), zorder=1)
    ax.plot([95, 95], [Y["cats"], Y["c2"]], color=EDGE, lw=0.6,
            ls=(0, (1, 1.6)), zorder=1)
    arrow(ax, (95, Y["cats"]), (X + W / 2 + 0.5, Y["cats"]), style="dotted", lw=0.6)

    save(fig, out_dir, "fig3-dispatch-path")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent)
    args = ap.parse_args()
    print(f"generating figures into {args.out}")
    fig1(args.out)
    fig2(args.out)
    fig3(args.out)
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
