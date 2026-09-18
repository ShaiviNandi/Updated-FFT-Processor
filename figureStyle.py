"""
figureStyle.py
==============

Publication figure style for the mixed-precision FFT paper.

WHY THIS EXISTS
---------------
Reviewer comment on Figs. 5-8: *"the fonts, legends, and markers are too small
at normal viewing scale - larger fonts and fewer plots per figure are needed to
make the reported trends and Pareto fronts readable."*

The root cause was not the font sizes in isolation - it was the combination of
a 6-panel 18x11 inch canvas scaled down to a ~3.5 inch journal column. A 10 pt
label on an 18 inch figure reproduced at 3.5 inches renders at

    10 pt x (3.5 / 18) = 1.9 pt

which is illegible at any viewing scale. The fix is therefore two-part and both
parts are necessary:

  1. author each figure at its FINAL printed size (so 11 pt means 11 pt), and
  2. put at most two panels in one figure, so each panel keeps its width.

WHAT THIS MODULE PROVIDES
-------------------------
  FIG_1COL / FIG_2COL      IEEE column widths in inches
  apply_paper_style()      the rcParams profile - call once at import
  TIMING, METRIC           validated colourblind-safe palettes
  panel_grid()             figure + axes sized for n panels at final scale
  scatter_by_timing()      the standard two-class scatter with big markers
  finish_panel()           labels, grid, legend, spine cleanup
  save_figure()            vector PDF + raster PNG, tight bbox

PALETTE VALIDATION
------------------
Run against the dataviz validator, light surface, categorical mode:

  TIMING  ("#0072B2", "#D55E00")
    lightness band PASS, chroma floor PASS,
    CVD separation PASS (worst adjacent dE 21.9 protan, 30.9 tritan),
    normal-vision floor PASS (dE 31.2), contrast vs surface PASS (both >= 3:1)

  METRIC  ("#0072B2", "#D55E00", "#009E73", "#7B3294")
    lightness PASS, chroma PASS, normal-vision floor PASS (worst dE 18.7),
    contrast PASS; CVD separation WARN at dE 6.7 deutan for the blue/purple
    pair. Acceptable here because the four metrics never share a panel - each
    is a single series on its own axes with a panel title naming it, so colour
    is decorative rather than identity-bearing. Do not reuse METRIC for four
    overlaid series without adding markers or direct labels.

  The previous palette ("#4CAF50" green / "#E91E63" pink) sat at dE 6.1 for
  deuteranopia and the green failed contrast at 2.71:1 - marginal on both
  counts, which is worth fixing while the figures are being redrawn anyway.

Print figures are authored for a white page, so there is no dark-mode variant;
identity is never carried by colour alone (marker shape differs as well), which
is what keeps the figures readable in greyscale print and for CVD readers.
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

# -----------------------------------------------------------------------------
# Canvas geometry - IEEE two-column template
# -----------------------------------------------------------------------------
FIG_1COL = 3.50          # inches, single-column figure
FIG_2COL = 7.16          # inches, double-column figure

PANEL_H_1COL = 2.95      # panel height for a single-column figure
PANEL_H_2COL = 3.15      # panel height for a two-panel double-column figure

MAX_PANELS_PER_FIGURE = 2   # the reviewer's "fewer plots per figure"

SAVE_DPI = 600           # raster fallback; the PDF is what goes in the paper
SAVE_FORMATS = ("pdf", "png")

# -----------------------------------------------------------------------------
# Type scale. These are TRUE points because figures are authored at final size.
# Bump FONT_SCALE if the venue reproduces small; everything tracks together.
# -----------------------------------------------------------------------------
FONT_SCALE = 1.0

# Body text in an IEEE two-column paper is 10 pt, so 9-10 pt here means the
# figure text matches the prose around it - the standard target. The reviewer's
# complaint was an effective ~2 pt, so this is a ~5x improvement.
FS_TICK   = 9.0 * FONT_SCALE
FS_LABEL  = 10.0 * FONT_SCALE
FS_TITLE  = 10.0 * FONT_SCALE
FS_LEGEND = 9.0 * FONT_SCALE
FS_ANNOT  = 8.0 * FONT_SCALE
FS_SUPTTL = 10.5 * FONT_SCALE

# -----------------------------------------------------------------------------
# Mark specs - thin marks, large enough to resolve in print
# -----------------------------------------------------------------------------
MARKER_AREA      = 110    # scatter `s`, ~10.5 px diameter
MARKER_AREA_SM   = 70     # when a panel is dense
MARKER_EDGE_W    = 0.8
LINE_W           = 2.2
LINE_MARKER_SIZE = 9.0
REF_LINE_W       = 1.8
GRID_ALPHA       = 0.30

# -----------------------------------------------------------------------------
# Palettes (validated - see module docstring)
# -----------------------------------------------------------------------------
TIMING = {
    "ok":      "#0072B2",   # meets timing
    "bad":     "#D55E00",   # violates timing
    "ok_mk":   "o",
    "bad_mk":  "X",
    "ref":     "#4D4D4D",   # reference/threshold lines - neutral ink, not a hue
}

METRIC = {
    "power":   "#0072B2",
    "area":    "#D55E00",
    "sqnr":    "#009E73",
    "latency": "#7B3294",
    "energy":  "#0072B2",
}

INK_PRIMARY   = "#1A1A1A"
INK_SECONDARY = "#4D4D4D"
INK_MUTED     = "#808080"
SURFACE       = "#FFFFFF"


# -----------------------------------------------------------------------------
# rcParams profile
# -----------------------------------------------------------------------------
def apply_paper_style():
    """Install the publication rcParams. Idempotent; call once at import."""
    plt.rcParams.update({
        # text
        "font.family":        "sans-serif",
        "font.sans-serif":    ["DejaVu Sans", "Helvetica", "Arial"],
        "font.size":          FS_TICK,
        "axes.labelsize":     FS_LABEL,
        "axes.titlesize":     FS_TITLE,
        "axes.labelweight":   "normal",
        "axes.titleweight":   "normal",
        "xtick.labelsize":    FS_TICK,
        "ytick.labelsize":    FS_TICK,
        "legend.fontsize":    FS_LEGEND,
        "figure.titlesize":   FS_SUPTTL,

        # colour of text and structure: ink tokens, never a series hue
        "text.color":         INK_PRIMARY,
        "axes.labelcolor":    INK_PRIMARY,
        "axes.edgecolor":     INK_SECONDARY,
        "xtick.color":        INK_SECONDARY,
        "ytick.color":        INK_SECONDARY,
        "xtick.labelcolor":   INK_PRIMARY,
        "ytick.labelcolor":   INK_PRIMARY,

        # recessive structure
        "axes.spines.top":    False,
        "axes.spines.right":  False,
        "axes.linewidth":     0.8,
        "xtick.major.width":  0.8,
        "ytick.major.width":  0.8,
        "xtick.major.size":   3.5,
        "ytick.major.size":   3.5,
        "grid.color":         INK_MUTED,
        "grid.linewidth":     0.6,
        "grid.linestyle":     "--",
        "grid.alpha":         GRID_ALPHA,

        # legend: readable, unobtrusive
        "legend.frameon":     True,
        "legend.framealpha":  0.92,
        "legend.edgecolor":   INK_MUTED,
        "legend.borderpad":   0.5,
        "legend.labelspacing": 0.4,
        "legend.handletextpad": 0.6,
        "legend.markerscale": 1.0,

        # output
        "figure.facecolor":   SURFACE,
        "axes.facecolor":     SURFACE,
        "savefig.facecolor":  SURFACE,
        "savefig.bbox":       "tight",
        "savefig.pad_inches": 0.02,
        "pdf.fonttype":       42,     # TrueType - editable/searchable, no Type-3
        "ps.fonttype":        42,
    })


apply_paper_style()


# -----------------------------------------------------------------------------
# Layout helpers
# -----------------------------------------------------------------------------
def panel_grid(n_panels, single_column=False, panel_h=None):
    """
    Return (fig, list_of_axes) sized at FINAL print dimensions.

    n_panels must be 1 or 2 - the whole point of the reviewer fix. Anything
    larger raises, so a future edit cannot quietly reintroduce a 2x3 grid.
    """
    if n_panels not in (1, 2):
        raise ValueError(
            f"n_panels={n_panels}: figures are limited to "
            f"{MAX_PANELS_PER_FIGURE} panels so each panel keeps its width at "
            "journal scale. Split into multiple figures instead."
        )

    if n_panels == 1:
        w = FIG_1COL if single_column else FIG_2COL / 2.0
        h = panel_h or PANEL_H_1COL
        fig, ax = plt.subplots(1, 1, figsize=(w, h))
        return fig, [ax]

    w = FIG_2COL
    h = panel_h or PANEL_H_2COL
    fig, axes = plt.subplots(1, 2, figsize=(w, h))
    return fig, list(axes)


def finish_panel(ax, xlabel, ylabel, title=None, legend_loc="best",
                 legend_handles=None, legend_ncol=1):
    """Apply labels, grid and legend consistently to one panel."""
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title, pad=6)
    ax.grid(True, which="major")
    ax.set_axisbelow(True)

    if legend_handles is not None:
        ax.legend(handles=legend_handles, loc=legend_loc, ncol=legend_ncol)
    elif legend_loc is not None:
        h, l = ax.get_legend_handles_labels()
        if h:
            ax.legend(loc=legend_loc, ncol=legend_ncol)


def timing_legend_handles(include_ref=None, show_ok=True, show_bad=True):
    """
    Shared legend for the two timing classes, with marker shape as a second
    encoding so identity survives greyscale print and colour-vision deficiency.

    `show_ok` / `show_bad` suppress a class that has no points in this figure -
    a legend entry for an empty class is noise and invites the reader to look
    for marks that are not there.
    """
    handles = []
    if show_ok:
        handles.append(
            Line2D([0], [0], marker=TIMING["ok_mk"], color="none",
                   markerfacecolor=TIMING["ok"], markeredgecolor="k",
                   markeredgewidth=MARKER_EDGE_W, markersize=LINE_MARKER_SIZE,
                   label="Meets timing"))
    if show_bad:
        handles.append(
            Line2D([0], [0], marker=TIMING["bad_mk"], color="none",
                   markerfacecolor=TIMING["bad"], markeredgecolor="k",
                   markeredgewidth=MARKER_EDGE_W, markersize=LINE_MARKER_SIZE,
                   label="Violates timing"))
    if include_ref:
        handles.append(
            Line2D([0], [0], color=TIMING["ref"], linestyle="--",
                   linewidth=REF_LINE_W, label=include_ref)
        )
    return handles


def scatter_by_timing(ax, x, y, meets_timing, dense=False):
    """
    The standard two-class Pareto scatter. Colour AND marker shape differ, and
    a 2 px white ring separates overlapping marks.
    """
    import numpy as np
    ok = np.asarray(meets_timing).astype(bool)
    s = MARKER_AREA_SM if dense else MARKER_AREA

    if ok.any():
        ax.scatter(np.asarray(x)[ok], np.asarray(y)[ok],
                   c=TIMING["ok"], marker=TIMING["ok_mk"], s=s,
                   alpha=0.85, edgecolors="white", linewidths=1.0, zorder=3)
    if (~ok).any():
        ax.scatter(np.asarray(x)[~ok], np.asarray(y)[~ok],
                   c=TIMING["bad"], marker=TIMING["bad_mk"], s=s * 1.15,
                   alpha=0.85, edgecolors="white", linewidths=1.0, zorder=4)


def add_threshold(ax, value, label, axis="y", data=None, headroom=1.6):
    """
    A neutral-ink reference line. Never a series hue.

    If `data` is given and the threshold sits further than `headroom` x the
    data range away, the line is NOT drawn - forcing it into view would squash
    the actual points into a corner, which is the exact readability failure the
    reviewer flagged. A corner annotation carries the same information without
    costing 60% of the panel.

    Returns True if a line was drawn (so the caller knows whether to put the
    threshold in the legend).
    """
    import numpy as np
    if data is not None:
        a = np.asarray(data, dtype=float)
        a = a[np.isfinite(a)]
        if a.size:
            lo, hi = float(a.min()), float(a.max())
            span = (hi - lo) or abs(hi) or 1.0
            if value > hi + headroom * span or value < lo - headroom * span:
                # Registered as a label-only legend entry rather than a fixed
                # ax.text box: matplotlib's "best" placement then keeps it off
                # the data and off the direct labels automatically.
                ax.plot([], [], color="none", linestyle="none", marker="",
                        label=f"all clear {label}")
                return False

    if axis == "y":
        ax.axhline(value, color=TIMING["ref"], linestyle="--",
                   linewidth=REF_LINE_W, zorder=2, label=label)
    else:
        ax.axvline(value, color=TIMING["ref"], linestyle="--",
                   linewidth=REF_LINE_W, zorder=2, label=label)
    return True


def log2_size_axis(ax, sizes):
    """
    Power-of-two x axis with non-colliding tick labels. Above 8 ticks the
    labels are rotated; 3- and 4-digit neighbours (512, 1024) otherwise overlap.
    """
    ax.set_xscale("log", base=2)
    ax.set_xticks(sizes)
    ax.set_xticklabels([f"{int(s)}" for s in sizes])
    if len(sizes) > 8:
        for t in ax.get_xticklabels():
            t.set_rotation(45)
            t.set_ha("right")
            t.set_rotation_mode("anchor")
    ax.minorticks_off()


def num_fmt(v):
    """Compact, non-scientific number formatting for direct labels."""
    av = abs(v)
    if av >= 1000:
        return f"{v:,.0f}"
    if av >= 100:
        return f"{v:.0f}"
    if av >= 10:
        return f"{v:.1f}"
    if av >= 1:
        return f"{v:.2f}"
    if av > 0:
        return f"{v:.3g}"
    return "0"


def save_figure(fig, out_dir, stem, formats=SAVE_FORMATS, dpi=SAVE_DPI,
                tighten=True):
    """
    Save one figure as vector PDF (for the paper) plus PNG (for previewing).
    Returns the list of written paths.
    """
    import os
    os.makedirs(out_dir, exist_ok=True)
    if tighten:
        fig.tight_layout()
    written = []
    for ext in formats:
        path = os.path.join(out_dir, f"{stem}.{ext}")
        fig.savefig(path, dpi=(dpi if ext == "png" else None))
        written.append(path)
    plt.close(fig)
    return written
