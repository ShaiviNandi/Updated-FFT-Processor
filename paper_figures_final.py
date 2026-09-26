#!/usr/bin/env python3
"""Paper figures for the mixed-precision FFT sweep.

Addresses the reviewer note on Figs 5-8 ("fonts, legends and markers too small
at normal viewing scale; larger fonts and fewer plots per figure"):

  * at most TWO panels per figure, never a 2x3 grid
  * authored at true IEEE width (7.16 in double column) so the typesetter does
    not downscale them and 10 pt stays 10 pt on the page
  * 10 pt labels, 9 pt ticks, >=8 px markers, 2 px lines
  * vector PDF (fonttype 42, real embedded text) plus a 400 dpi PNG
  * two-colour categorical palette validated CVD-safe (Okabe-Ito blue/orange,
    worst adjacent dE 21.9 protan / 31.2 normal vision), with hatching as a
    secondary encoding so FP4/FP8 survives greyscale printing
  * one y-axis per panel, never a dual axis

Reads results/fft_<N>/all_solutions_fft<N>_fixed.csv. Writes to figures/.
"""
import csv, glob, math, os, re, sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap, ListedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

FP4, FP8 = "#0072B2", "#D55E00"
INK, INK2, GRID = "#141920", "#4B5666", "#D7DDE5"
NEUTRAL = "#9AA6B4"
SEQ = LinearSegmentedColormap.from_list("fp8seq", ["#DCE9F2", "#0072B2", "#003F63"])
W2 = 7.16

plt.rcParams.update({
    "figure.dpi": 150, "savefig.dpi": 400, "savefig.bbox": "tight",
    "savefig.pad_inches": 0.02, "pdf.fonttype": 42, "ps.fonttype": 42,
    "font.family": "sans-serif", "font.sans-serif": ["DejaVu Sans"],
    "font.size": 10, "axes.labelsize": 10, "axes.titlesize": 10,
    "xtick.labelsize": 9, "ytick.labelsize": 9,
    "legend.fontsize": 9, "legend.frameon": False,
    "axes.edgecolor": INK2, "axes.linewidth": 0.8,
    "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "grid.linestyle": "-", "axes.axisbelow": True,
    "lines.linewidth": 2.0, "lines.markersize": 6,
    "axes.spines.top": False, "axes.spines.right": False,
})
OUT = "figures"

def num(s):
    try: return float(s)
    except (TypeError, ValueError): return None

def load(root="results"):
    data = {}
    for d in sorted(glob.glob(os.path.join(root, "fft_*"))):
        m = re.match(r"fft_(\d+)$", os.path.basename(d))
        if not m: continue
        n = int(m.group(1))
        p = os.path.join(d, f"all_solutions_fft{n}_fixed.csv")
        if not os.path.exists(p):
            p = os.path.join(d, f"all_solutions_fft{n}.csv")
            if not os.path.exists(p): continue
            print(f"  note: N={n} has no _fixed file, using the original", file=sys.stderr)
        rows = []
        with open(p) as f:
            rd = csv.DictReader(f)
            mult = sorted((c for c in rd.fieldnames if re.match(r"s\d+_mult$", c)),
                          key=lambda c: int(re.match(r"s(\d+)", c).group(1)))
            add  = sorted((c for c in rd.fieldnames if re.match(r"s\d+_add$", c)),
                          key=lambda c: int(re.match(r"s(\d+)", c).group(1)))
            for r in rd:
                r["_mult"] = [int(r[c]) for c in mult if (r[c] or "").strip() != ""]
                r["_add"]  = [int(r[c]) for c in add  if (r[c] or "").strip() != ""]
                r["_pf"]   = (r.get("on_pareto_front") or "0").strip() == "1"
                r["_e"]    = num(r.get("energy_pJ"))
                if r["_e"] is None:
                    p_w, cyc = num(r.get("power_W")), num(r.get("avg_exec_cycles"))
                    r["_e"] = p_w * cyc * 1e4 if (p_w and cyc) else None
                r["_sqnr"] = num(r.get("sqnr_dB"))
                r["_lut"]  = num(r.get("area_LUTs"))
                r["_cd"]   = num(r.get("crit_delay_ns"))
                r["_fmax"] = 1000.0 / r["_cd"] if r["_cd"] and r["_cd"] > 0 else None
                r["_nfp8"] = sum(r["_mult"])
                tot = len(r["_mult"]) + len(r["_add"])
                r["_frac8"] = (sum(r["_mult"]) + sum(r["_add"])) / tot if tot else 0.0
                rows.append(r)
        if rows: data[n] = rows
    return data

def finish(fig, name):
    os.makedirs(OUT, exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(OUT, f"{name}.{ext}"))
    plt.close(fig); print(f"  wrote {OUT}/{name}.pdf and .png")

def nice(ax, xlab, ylab, title=None):
    ax.set_xlabel(xlab); ax.set_ylabel(ylab)
    if title: ax.set_title(title, loc="left", pad=6)

def fig_front(data, sizes=(256, 1024)):
    sizes = [n for n in sizes if n in data]
    if not sizes: return
    fig, axes = plt.subplots(1, len(sizes), figsize=(W2, 2.9), constrained_layout=True)
    axes = np.atleast_1d(axes)
    for ax, n in zip(axes, sizes):
        rows = [r for r in data[n] if r["_e"] and r["_sqnr"] is not None]
        off = [r for r in rows if not r["_pf"]]; on = [r for r in rows if r["_pf"]]
        if off:
            ax.scatter([r["_sqnr"] for r in off], [r["_e"]/1e6 for r in off],
                       s=26, c=NEUTRAL, alpha=.55, linewidths=0, zorder=2)
        if on:
            sc = ax.scatter([r["_sqnr"] for r in on], [r["_e"]/1e6 for r in on],
                            s=64, c=[r["_frac8"] for r in on], cmap=SEQ,
                            vmin=0, vmax=1, edgecolors="white", linewidths=1.1, zorder=4)
            lo = min(on, key=lambda r: r["_e"]); hi = max(on, key=lambda r: r["_sqnr"])
            for r, txt, dy in ((lo, "lowest energy", 8), (hi, "highest SQNR", -14)):
                ax.annotate(txt, (r["_sqnr"], r["_e"]/1e6), textcoords="offset points",
                            xytext=(4, dy), fontsize=8.5, color=INK2)
        nice(ax, "SQNR (dB)", "Energy per transform (µJ)", f"N = {n}")
        ax.margins(x=.10, y=.12)
    cb = fig.colorbar(sc, ax=axes.tolist(), pad=0.015, aspect=28)
    cb.set_label("fraction of genes at FP8", size=9); cb.ax.tick_params(labelsize=8)
    axes[0].legend(handles=[
        Line2D([], [], marker="o", linestyle="none", markersize=5,
               color=NEUTRAL, alpha=.65, label="dominated"),
        Line2D([], [], marker="o", linestyle="none", markersize=8,
               markerfacecolor="#2E86C1", markeredgecolor="white",
               markeredgewidth=1.1, color="none", label="Pareto-optimal")],
        loc="upper left", handletextpad=.4, borderpad=.2)
    finish(fig, "fig5_pareto_energy_sqnr")

def fig_scaling(data):
    ns = sorted(data)
    if len(ns) < 2: return
    best_e, best_s = [], []
    for n in ns:
        rows = [r for r in data[n] if r["_e"]]
        pf = [r for r in rows if r["_pf"]] or rows
        best_e.append(min(r["_e"] for r in pf) / 1e6)
        sq = [r["_sqnr"] for r in rows if r["_sqnr"] is not None]
        best_s.append(max(sq) if sq else np.nan)
    x = np.log2(ns)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(W2, 2.9), constrained_layout=True)
    a1.plot(x, best_e, "-o", color=FP4, markeredgecolor="white", markeredgewidth=1)
    a1.set_yscale("log")
    nice(a1, "Transform size N", "Lowest energy per transform (µJ)", "(a) Energy scaling")
    a2.plot(x, best_s, "-s", color=FP8, markeredgecolor="white", markeredgewidth=1)
    nice(a2, "Transform size N", "Best achievable SQNR (dB)", "(b) Accuracy ceiling")
    for ax in (a1, a2):
        ax.set_xticks(x); ax.set_xticklabels([str(n) for n in ns], rotation=45, ha="right")
    finish(fig, "fig6_scaling")

def fig_schedule(data):
    ns = [n for n in sorted(data) if n >= 16]
    if not ns: return
    picks = {}
    for n in ns:
        rows = [r for r in data[n] if r["_e"]]
        pf = [r for r in rows if r["_pf"]] or rows
        picks[n] = min(pf, key=lambda r: r["_e"])
    smax = max(len(picks[n]["_mult"]) for n in ns)
    cmap = ListedColormap([FP4, FP8])
    fig, axes = plt.subplots(1, 2, figsize=(W2, 3.1), constrained_layout=True)
    for ax, key, lab in zip(axes, ("_mult", "_add"),
                            ("(a) Multiplier precision", "(b) Adder precision")):
        grid = np.full((len(ns), smax), np.nan)
        for i, n in enumerate(ns):
            v = picks[n][key]; grid[i, :len(v)] = v
        ax.imshow(np.ma.masked_invalid(grid), cmap=cmap, vmin=0, vmax=1,
                  aspect="auto", interpolation="nearest")
        for i in range(len(ns)):
            for j in range(smax):
                if grid[i, j] == 1:
                    ax.add_patch(plt.Rectangle((j-.5, i-.5), 1, 1, fill=False,
                                               hatch="///", edgecolor="white",
                                               linewidth=0, alpha=.55))
        ax.set_xticks(range(smax)); ax.set_xticklabels(range(smax))
        ax.set_yticks(range(len(ns))); ax.set_yticklabels(ns)
        ax.set_xlabel("FFT stage index"); ax.set_ylabel("Transform size N")
        ax.set_title(lab, loc="left", pad=6); ax.grid(False)
        ax.set_xticks(np.arange(-.5, smax, 1), minor=True)
        ax.set_yticks(np.arange(-.5, len(ns), 1), minor=True)
        ax.grid(which="minor", color="white", linewidth=1.4)
        ax.tick_params(which="minor", length=0)
    fig.legend(handles=[Patch(facecolor=FP4, label="FP4 (E2M1)"),
                        Patch(facecolor=FP8, hatch="///", edgecolor="white",
                              label="FP8 (E4M3)")],
               loc="outside upper center", ncol=2)
    finish(fig, "fig7_precision_schedule")

def fig_area_freq(data, n=256):
    if n not in data: return
    rows = [r for r in data[n] if r["_lut"] and r["_lut"] > 0]
    if not rows: return
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(W2, 2.9), constrained_layout=True)
    k = np.array([r["_nfp8"] for r in rows], float)
    jit = (np.random.default_rng(0).random(len(k)) - .5) * .28
    a1.scatter(k + jit, [r["_lut"] for r in rows], s=46, c=FP4, alpha=.75,
               edgecolors="white", linewidths=.8)
    ks = sorted(set(int(v) for v in k))
    med = [np.median([r["_lut"] for r in rows if r["_nfp8"] == v]) for v in ks]
    a1.plot(ks, med, color=INK, linewidth=1.6, zorder=5, label="median")
    nice(a1, "Stages using FP8 multiply", "Area (LUTs)", "(a) Area: one step, then flat")
    a1.legend(loc="lower right")
    fm = [r for r in rows if r["_fmax"]]
    a2.scatter([r["_nfp8"] + j for r, j in zip(fm, jit[:len(fm)])],
               [r["_fmax"] for r in fm], s=46, c=FP8, alpha=.75,
               edgecolors="white", linewidths=.8)
    nice(a2, "Stages using FP8 multiply", "Achievable $f_{max}$ (MHz)",
         "(b) $f_{max}$: the same single step")
    finish(fig, "fig8_area_frequency")

def timing_table(data):
    print("\nTiming, measured (fmax = 1000 / crit_delay_ns)")
    print(f"{'N':>6} {'all-FP4 fmax':>13} {'any-FP8 fmax':>13} "
          f"{'all-FP4 crit':>13} {'any-FP8 crit':>13}")
    print("-" * 62)
    for n in sorted(data):
        rows = data[n]
        f4 = [r for r in rows if r["_nfp8"] == 0 and r["_fmax"]]
        f8 = [r for r in rows if r["_nfp8"] > 0 and r["_fmax"]]
        g = lambda xs, k: (f"{max(x[k] for x in xs):.1f}" if xs else "-")
        c = lambda xs: (f"{min(x['_cd'] for x in xs):.2f}" if xs else "-")
        print(f"{n:>6} {g(f4,'_fmax'):>13} {g(f8,'_fmax'):>13} "
              f"{c(f4):>13} {c(f8):>13}")

if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "results"
    data = load(root)
    if not data: print(f"no data under {root!r}"); sys.exit(1)
    print(f"loaded {len(data)} sizes: {sorted(data)}")
    fig_front(data); fig_scaling(data); fig_schedule(data); fig_area_freq(data)
    timing_table(data)
