#!/usr/bin/env python3
"""
render_demo_figures.py -- render the new paper figures from the REAL result
CSVs already in the repo, so the layout can be eyeballed before the next sweep.

Usage:
    python3 render_demo_figures.py [results_root] [out_dir]

Defaults to ./results and ./results/_figures_preview.
"""

import csv
import math
import os
import sys

import numpy as np

import figureStyle as fs
import paperFigures as pf


def load_solutions(path):
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8-sig")))
    if not rows:
        raise SystemExit(f"no rows in {path}")
    g = lambda r, k, d=0.0: float(r.get(k, d) or d)
    return dict(
        power=np.array([g(r, "power_W") for r in rows]),
        area=np.array([g(r, "area_LUTs") for r in rows]),
        sqnr=np.array([g(r, "sqnr_dB") for r in rows]),
        nlat=np.array([g(r, "norm_latency") for r in rows]),
        delay=np.array([g(r, "crit_delay_ns") for r in rows]),
    )


def to_weighted_objectives(d):
    """Invert paperFigures.decode_objectives so real data flows through it."""
    o0 = d["power"] / pf.REF_POWER_W * pf.WEIGHT_POWER
    o1 = d["area"] / pf.REF_AREA_LUTS * pf.WEIGHT_AREA
    perf = ((pf.SQNR_OFFSET - d["sqnr"]) / pf.REF_SQNR_RANGE) ** 2
    o2 = perf * pf.WEIGHT_PERFORMANCE
    o3 = d["nlat"] / pf.REF_LATENCY * pf.WEIGHT_LATENCY
    return np.column_stack([o0, o1, o2, o3])


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "results"
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(root, "_figures_preview")
    os.makedirs(out, exist_ok=True)

    written = []

    # --- Figs 5-7 from the N=256 Pareto set -------------------------------
    sol = os.path.join(root, "fft_256", "all_solutions_fft256.csv")
    if os.path.exists(sol):
        d = load_solutions(sol)
        obj = to_weighted_objectives(d)
        print(f"N=256: {len(obj)} solutions, "
              f"delay {d['delay'].min():.1f}-{d['delay'].max():.1f} ns, "
              f"LUT {d['area'].min():.0f}-{d['area'].max():.0f}")

        # real target (80 ns): every design passes
        written += pf.plot_pareto_figures(obj, 256, out, feasible=True)

        # 30 ns target: exercises both timing classes so the two-class
        # rendering, hatching and shared legend can be checked
        orig = pf.REFERENCE_CLOCK_PERIOD_NS
        pf.REFERENCE_CLOCK_PERIOD_NS = 30.0
        sub = os.path.join(out, "target30ns")
        written += pf.plot_pareto_figures(obj, 256, sub, feasible=True)
        pf.REFERENCE_CLOCK_PERIOD_NS = orig
    else:
        print(f"skip Figs 5-7: {sol} not found")

    # --- Fig 8 across FFT sizes -------------------------------------------
    best = os.path.join(root, "..", "best_chromosomes.csv")
    if not os.path.exists(best):
        best = "best_chromosomes.csv"
    if os.path.exists(best):
        rows = list(csv.DictReader(open(best, newline="", encoding="utf-8-sig")))
        rows.sort(key=lambda r: float(r["fft_size"]))
        sizes = [float(r["fft_size"]) for r in rows]
        written += pf.plot_size_comparison_figures(
            sizes,
            [float(r["power_W"]) for r in rows],
            [float(r["area_LUTs"]) for r in rows],
            [float(r["sqnr_dB"]) for r in rows],
            [float(r["crit_delay_ns"]) for r in rows],
            out,
        )
    else:
        print("skip Fig 8: best_chromosomes.csv not found")

    print(f"\n{len(written)} file(s) written:")
    for p in written:
        print("  ", p, f"({os.path.getsize(p)/1024:.0f} KB)")


if __name__ == "__main__":
    main()
