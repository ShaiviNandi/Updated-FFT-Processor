"""
paperFigures.py
===============

Replacement figure generators for the mixed-precision FFT paper, addressing the
reviewer comment on Figs. 5-8 (fonts, legends and markers too small; too many
plots per figure).

WHAT CHANGED VS THE ORIGINALS IN runMixedFFTOptimization.py
-----------------------------------------------------------
  * Panels per figure: 6 -> 2 (Fig. 5), 3 -> 2+1 (Fig. 7), 4 -> 2+2 (Fig. 8).
    `figureStyle.panel_grid` raises if anyone asks for more than 2, so the
    regression cannot silently return.
  * Canvas: 18x11 in -> 7.16x3.15 in, i.e. authored at the IEEE double-column
    width the figure is actually printed at. Combined with the panel split this
    is what makes the type legible - a 10 pt label on an 18 in canvas
    reproduced at 3.5 in renders at 1.9 pt.
  * Type: 8-10 pt nominal on an oversized canvas -> 10-11.5 pt true points.
    Effective magnification at print size is roughly 3x.
  * Markers: s=60 -> s=110, with a white separation ring, and marker SHAPE now
    differs between the two timing classes so identity is not colour-alone.
  * Legends: 8 pt, per-panel, duplicated -> 10.5 pt, one shared legend built by
    `figureStyle.timing_legend_handles`, including the threshold line.
  * Palette: green/pink (dE 6.1 deuteranopia, green at 2.71:1 contrast) ->
    blue/vermillion (dE 21.9, both >= 3:1). Validated - see figureStyle.
  * 3-D scatter replaced by a 2-D scatter with a single-hue sequential colour
    ramp for critical-path delay. A rotated 3-D projection loses its axis
    labels at column width and cannot be read off; the 2-D version carries the
    same three variables.
  * Direct labels are now selective (first, last, extremum) instead of one
    number on every point.
  * Panels whose x or y data is constant are dropped automatically, with a
    logged note. After the switch to the 3-objective energy formulation, area
    is constant by construction; plotting it as an axis would be misleading.
  * Output is vector PDF plus a 600 dpi PNG. The PDF is what belongs in LaTeX;
    the previous 200 dpi PNG was the other half of the legibility problem.

FIGURE MAP (suggested caption numbering)
----------------------------------------
  Fig. 5a  pareto_fft<N>_accuracy   cost vs SQNR | SQNR vs critical-path delay
  Fig. 5b  pareto_fft<N>_area       cost vs area | area vs SQNR          (4-obj only)
  Fig. 5c  pareto_fft<N>_timing     cost vs delay | area vs delay
  Fig. 6   pareto_fft<N>_3var       SQNR vs cost, colour = delay
  Fig. 7a  delay_fft<N>_dist        delay distribution | delay vs SQNR
  Fig. 7b  delay_fft<N>_bubble      delay vs cost, bubble area = LUTs
  Fig. 8a  sweep_cost_accuracy      min cost vs N | max SQNR vs N
  Fig. 8b  sweep_area_timing        min area vs N | min delay vs N

STATUS: rendered and eyeballed against the real N=256 and multi-size result
CSVs in this repo. Not yet run inside a live NSGA-II sweep - the wiring into
runMixedFFTOptimization.py is three import/call edits, listed at the bottom of
this docstring.

INTEGRATION
-----------
In runMixedFFTOptimization.py:

  1. after the existing matplotlib imports, add:
         import figureStyle                       # installs the rcParams
         from paperFigures import (plot_pareto_figures,
                                   plot_size_comparison_figures)

  2. replace the body of `plot_pareto_front(...)` with:
         plot_pareto_figures(pareto_objectives, fft_size, results_subdir,
                             feasible=feasible)

  3. in the multi-size comparison block (the `fig, axes = plt.subplots(2, 2,
     figsize=(16, 11))` section), replace it with:
         plot_size_comparison_figures(sizes, best_power, best_area,
                                      best_sqnr, best_delay, RESULTS_DIR)

Keep the old functions around under a `_legacy_` prefix until the new figures
are in the manuscript.
"""

import math
import os

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

import figureStyle as fs
from figureStyle import (
    TIMING, METRIC, INK_SECONDARY, INK_MUTED,
    panel_grid, finish_panel, timing_legend_handles, scatter_by_timing,
    add_threshold, save_figure, log2_size_axis, num_fmt,
    MARKER_AREA, MARKER_EDGE_W, LINE_W, LINE_MARKER_SIZE, REF_LINE_W,
    FS_ANNOT,
)

# -----------------------------------------------------------------------------
# Optional coupling to the project globals. Defaults keep this importable and
# testable on its own.
# -----------------------------------------------------------------------------
try:
    from globalVariablesMixedFFT import (
        REFERENCE_CLOCK_PERIOD_NS, FPGA_DEVICE,
        WEIGHT_POWER, WEIGHT_AREA, WEIGHT_PERFORMANCE, WEIGHT_LATENCY,
        REF_POWER_W, REF_AREA_LUTS, REF_SQNR_RANGE, SQNR_OFFSET, REF_LATENCY,
        log_message,
    )
except Exception:                                     # standalone / testing
    REFERENCE_CLOCK_PERIOD_NS = 80.0
    FPGA_DEVICE = "xc7a35tcpg236-1"
    WEIGHT_POWER = WEIGHT_AREA = 1.0
    WEIGHT_PERFORMANCE = 30.0
    WEIGHT_LATENCY = 8.0
    REF_POWER_W = 3.0
    REF_AREA_LUTS = 5000
    REF_SQNR_RANGE = 50.0
    SQNR_OFFSET = 50.0
    REF_LATENCY = 10.0

    def log_message(msg, level="INFO"):
        print(f"[{level}] {msg}")


# Single-hue sequential ramp for critical-path delay, light -> dark, truncated
# so the light end still has contrast against a white page.
_DELAY_CMAP = LinearSegmentedColormap.from_list(
    "delay_blues", ["#CBE3F2", "#7FB9DD", "#3A8DBF", "#0072B2", "#00436A"]
)


# =============================================================================
# Objective decoding
# =============================================================================
def decode_objectives(pareto_objectives, fft_size, crit_delay_fn=None):
    """
    Turn a weighted pymoo objective matrix into physical quantities.

    Handles both layouts:
      4 objectives (legacy): [power, area, sqnr_err^2, norm_latency]
      3 objectives (energy): [energy, sqnr_err^2, norm_latency]

    Returns a dict of 1-D arrays. `cost` is whichever of power/energy this run
    optimised, with `cost_label` naming it.
    """
    obj = np.asarray(pareto_objectives, dtype=float)
    if obj.ndim == 1:
        obj = obj.reshape(1, -1)
    n_obj = obj.shape[1]

    if n_obj >= 4:
        cost = (obj[:, 0] / WEIGHT_POWER) * REF_POWER_W
        cost_label = "Power (W)"
        area = (obj[:, 1] / WEIGHT_AREA) * REF_AREA_LUTS
        perf = obj[:, 2] / WEIGHT_PERFORMANCE
        nlat = (obj[:, 3] / WEIGHT_LATENCY) * REF_LATENCY
        has_area = True
    else:
        try:
            from energyObjective import WEIGHT_ENERGY, REF_ENERGY_PJ
            w_e = WEIGHT_ENERGY
            r_e = REF_ENERGY_PJ or 1.0
        except Exception:
            w_e, r_e = 2.0, 1.0
        cost = (obj[:, 0] / w_e) * r_e
        cost_label = "Energy (pJ/transform)"
        area = np.full(len(obj), np.nan)
        perf = obj[:, 1] / WEIGHT_PERFORMANCE
        nlat = (obj[:, 2] / WEIGHT_LATENCY) * REF_LATENCY
        has_area = False

    sqnr = np.array([SQNR_OFFSET - (math.sqrt(max(0.0, p)) * REF_SQNR_RANGE)
                     for p in perf])
    sqnr = np.where(np.isfinite(sqnr), sqnr, np.nan)

    if crit_delay_fn is not None:
        delay = np.array([crit_delay_fn(v, fft_size) for v in nlat])
    else:
        num_stages = max(1, int(math.log2(max(2, fft_size))))
        pipeline_factor = max(1.0, num_stages / 6.0)
        delay = (nlat / pipeline_factor) * REFERENCE_CLOCK_PERIOD_NS

    return {
        "cost": cost, "cost_label": cost_label, "has_area": has_area,
        "area": area, "sqnr": sqnr, "norm_latency": nlat, "delay": delay,
        "meets_timing": (delay <= REFERENCE_CLOCK_PERIOD_NS).astype(int),
        "n": len(obj),
    }


def _informative(arr):
    """True when an array carries variance worth putting on an axis."""
    a = np.asarray(arr, dtype=float)
    a = a[np.isfinite(a)]
    if a.size < 2:
        return False
    if np.nanstd(a) == 0:
        return False
    m = np.nanmean(np.abs(a))
    return not (m > 0 and np.nanstd(a) / m < 1e-6)


def _selective_labels(ax, x, y, fmt=None, color=INK_SECONDARY):
    """
    Direct-label only the first point, the last point and the extremum -
    never a number on every marker.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(x) == 0:
        return
    idx = {0, len(x) - 1, int(np.nanargmax(y)), int(np.nanargmin(y))}
    for i in sorted(idx):
        if not np.isfinite(y[i]):
            continue
        txt = fmt.format(y[i]) if fmt else num_fmt(y[i])
        ax.annotate(txt, (x[i], y[i]),
                    textcoords="offset points", xytext=(0, 9),
                    ha="center", fontsize=FS_ANNOT, color=color,
                    clip_on=False)
    # headroom so the topmost direct label is not clipped by the canvas edge
    ax.margins(y=0.16)


# =============================================================================
# Fig. 5 - the Pareto fronts, split across figures
# =============================================================================
def plot_pareto_figures(pareto_objectives, fft_size, out_dir, feasible=True,
                        crit_delay_fn=None):
    """
    Drop-in replacement for plot_pareto_front. Emits several small figures
    instead of one 6-panel sheet. Returns the list of written paths.
    """
    if pareto_objectives is None or len(pareto_objectives) == 0:
        return []

    d = decode_objectives(pareto_objectives, fft_size, crit_delay_fn)
    n = d["n"]
    pct_ok = 100.0 * int(np.sum(d["meets_timing"])) / max(1, n)
    tag = "pareto" if feasible else "infeasible"
    written = []

    _ok_mask = d["meets_timing"].astype(bool)
    _any_ok, _any_bad = bool(_ok_mask.any()), bool((~_ok_mask).any())

    delay_label = "Critical-path delay (ns)"
    ref_label = f"{REFERENCE_CLOCK_PERIOD_NS:.0f} ns clock target"

    # --- groups of at most two panels -------------------------------------
    groups = [
        ("accuracy", [
            (d["cost"],  d["sqnr"],  d["cost_label"], "SQNR (dB)",   None),
            (d["sqnr"],  d["delay"], "SQNR (dB)",     delay_label,   "y"),
        ]),
        ("timing", [
            (d["cost"],  d["delay"], d["cost_label"], delay_label,   "y"),
            (d["area"],  d["delay"], "Area (LUTs)",   delay_label,   "y"),
        ]),
    ]
    if d["has_area"]:
        groups.insert(1, ("area", [
            (d["cost"], d["area"], d["cost_label"], "Area (LUTs)", None),
            (d["area"], d["sqnr"], "Area (LUTs)",   "SQNR (dB)",   None),
        ]))

    for gname, panels in groups:
        keep = [p for p in panels if _informative(p[0]) and _informative(p[1])]
        dropped = len(panels) - len(keep)
        if dropped:
            log_message(
                f"figure {tag}_fft{fft_size}_{gname}: dropped {dropped} panel(s) "
                "whose data is constant (no information on that axis)",
                level="WARN")
        if not keep:
            continue

        fig, axes = panel_grid(len(keep))
        _drew_ref, _note_handles = [], []
        for ax, (xd, yd, xl, yl, thr_axis) in zip(axes, keep):
            scatter_by_timing(ax, xd, yd, d["meets_timing"], dense=(n > 40))
            drew = False
            if thr_axis:
                drew = add_threshold(ax, REFERENCE_CLOCK_PERIOD_NS, ref_label,
                                     axis=thr_axis,
                                     data=(yd if thr_axis == "y" else xd))
            _drew_ref.append(drew)
            finish_panel(ax, xl, yl, title=None, legend_loc=None)
            _note_handles.extend(
                h for h, l in zip(*ax.get_legend_handles_labels())
                if l.startswith("all clear "))

        handles = timing_legend_handles(
            include_ref=ref_label if any(_drew_ref) else None,
            show_ok=_any_ok, show_bad=_any_bad)
        axes[0].legend(handles=handles + _note_handles[:1], loc="best")

        written += save_figure(fig, out_dir, f"{tag}_fft{fft_size}_{gname}")

    # --- Fig. 6: three variables in two dimensions ------------------------
    if _informative(d["cost"]) and _informative(d["sqnr"]):
        fig, (ax,) = panel_grid(1)
        vmax = float(np.nanmax(d["delay"])) if np.isfinite(d["delay"]).any() else 1.0
        sc = ax.scatter(d["cost"], d["sqnr"], c=d["delay"], cmap=_DELAY_CMAP,
                        vmin=0.0, vmax=vmax, s=MARKER_AREA, alpha=0.9,
                        edgecolors="white", linewidths=1.0, zorder=3)
        cbar = fig.colorbar(sc, ax=ax, pad=0.02)
        cbar.set_label(delay_label)
        cbar.ax.axhline(REFERENCE_CLOCK_PERIOD_NS, color=TIMING["ref"],
                        linewidth=REF_LINE_W, linestyle="--")
        cbar.ax.tick_params(labelsize=fs.FS_TICK)
        finish_panel(ax, d["cost_label"], "SQNR (dB)", legend_loc=None)
        written += save_figure(fig, out_dir, f"{tag}_fft{fft_size}_3var")

    # --- Fig. 7a: delay distribution + delay vs accuracy ------------------
    if _informative(d["delay"]):
        fig, axes = panel_grid(2)

        ax = axes[0]
        ok = d["meets_timing"].astype(bool)
        bins = int(min(20, max(5, n // 3)))
        if ok.any():
            ax.hist(d["delay"][ok], bins=bins, color=TIMING["ok"],
                    alpha=0.85, edgecolor="white", linewidth=0.8,
                    label="Meets timing")
        if (~ok).any():
            ax.hist(d["delay"][~ok], bins=bins, color=TIMING["bad"],
                    alpha=0.85, edgecolor="white", linewidth=0.8,
                    hatch="///", label="Violates timing")
        # hatching gives the histogram a second encoding too, for greyscale print
        add_threshold(ax, REFERENCE_CLOCK_PERIOD_NS, ref_label, axis="x",
                      data=d["delay"])
        finish_panel(ax, delay_label, "Number of designs", legend_loc="best")

        ax = axes[1]
        scatter_by_timing(ax, d["sqnr"], d["norm_latency"], d["meets_timing"],
                          dense=(n > 40))
        add_threshold(ax, 1.0, "Timing budget = 1.0", axis="y")
        finish_panel(ax, "SQNR (dB)",
                     f"Normalised latency (x{REFERENCE_CLOCK_PERIOD_NS:.0f} ns)",
                     legend_loc=None)
        axes[1].legend(handles=timing_legend_handles(
            include_ref="Timing budget = 1.0",
            show_ok=_any_ok, show_bad=_any_bad), loc="best")

        written += save_figure(fig, out_dir, f"{tag}_fft{fft_size}_delay_dist")

    # --- Fig. 7b: delay vs cost, bubble area = LUTs -----------------------
    if d["has_area"] and _informative(d["area"]) and _informative(d["delay"]):
        fig, (ax,) = panel_grid(1)
        a = d["area"]
        span = float(np.nanmax(a) - np.nanmin(a))
        a_norm = (a - np.nanmin(a)) / (span if span else 1.0)
        sizes = 60 + 260 * a_norm
        ok = d["meets_timing"].astype(bool)
        for mask, col, mk in ((ok, TIMING["ok"], TIMING["ok_mk"]),
                              (~ok, TIMING["bad"], TIMING["bad_mk"])):
            if mask.any():
                ax.scatter(d["cost"][mask], d["delay"][mask], s=sizes[mask],
                           c=col, marker=mk, alpha=0.8, edgecolors="white",
                           linewidths=1.0, zorder=3)
        _drew = add_threshold(ax, REFERENCE_CLOCK_PERIOD_NS, ref_label,
                              axis="y", data=d["delay"])
        finish_panel(ax, d["cost_label"], delay_label, legend_loc=None)
        ax.legend(handles=timing_legend_handles(
                      include_ref=(ref_label if _drew else None),
                                                show_ok=_any_ok,
                                                show_bad=_any_bad), loc="best")
        ax.text(0.98, 0.02, "marker area $\\propto$ LUTs", transform=ax.transAxes,
                ha="right", va="bottom", fontsize=FS_ANNOT, color=INK_MUTED)
        written += save_figure(fig, out_dir, f"{tag}_fft{fft_size}_delay_bubble")

    log_message(f"FFT-{fft_size}: {len(written)} figure file(s) written to "
                f"{out_dir} ({n} solutions, {pct_ok:.0f}% meet timing)")
    return written


# =============================================================================
# Fig. 8 - across-FFT-size trends, split 2 + 2
# =============================================================================
def plot_size_comparison_figures(sizes, best_cost, best_area, best_sqnr,
                                 best_delay, out_dir, cost_label="Min power (W)"):
    """
    Replacement for the 2x2 `comparison_all_fft_sizes.png`. Emits two
    two-panel figures so each trend keeps its width at column scale.
    """
    if not len(sizes):
        return []

    sizes = np.asarray(sizes, dtype=float)
    written = []

    def _trend(ax, y, ylabel, color, threshold=None, thr_label=None):
        y = np.asarray(y, dtype=float)
        ax.plot(sizes, y, marker="o", color=color, linewidth=LINE_W,
                markersize=LINE_MARKER_SIZE, markeredgecolor="white",
                markeredgewidth=MARKER_EDGE_W, zorder=3)
        drew = False
        if threshold is not None:
            drew = add_threshold(ax, threshold, thr_label, axis="y", data=y)
        log2_size_axis(ax, sizes)
        _selective_labels(ax, sizes, y, color=color)
        finish_panel(ax, "FFT size $N$ (points)", ylabel, legend_loc="best")

    # 8a: cost and accuracy
    fig, axes = panel_grid(2)
    _trend(axes[0], best_cost, cost_label, METRIC["power"])
    _trend(axes[1], best_sqnr, "Max SQNR (dB)", METRIC["sqnr"])
    written += save_figure(fig, out_dir, "sweep_cost_accuracy")

    # 8b: area and timing
    area_ok = best_area is not None and _informative(best_area)
    panels = []
    if area_ok:
        panels.append(("area", best_area, "Min area (LUTs)", METRIC["area"], None, None))
    panels.append(("delay", best_delay, "Min critical-path delay (ns)",
                   METRIC["latency"], REFERENCE_CLOCK_PERIOD_NS,
                   f"{REFERENCE_CLOCK_PERIOD_NS:.0f} ns clock target"))

    fig, axes = panel_grid(len(panels))
    for ax, (_k, y, yl, col, thr, thrl) in zip(axes, panels):
        _trend(ax, y, yl, col, threshold=thr, thr_label=thrl)
    written += save_figure(fig, out_dir, "sweep_area_timing")

    if not area_ok:
        log_message("sweep_area_timing: area panel dropped - area is constant "
                    "across FFT sizes in this run", level="WARN")

    log_message(f"cross-size comparison: {len(written)} figure file(s) -> {out_dir}")
    return written
