#!/usr/bin/env python3
"""
Three-way comparison: FP16 baseline vs FP32 baseline vs mixed FP4/FP8
=====================================================================
Joins the three FPGA metric CSVs into one table per figure of merit, with the
mixed core's advantage expressed as a ratio. It measures nothing itself: run the
three drivers first, then this.

    python3 fp16_baseline/synth/run_fp16_fpga.py
    python3 fp32_baseline/synth/run_fp32_fpga.py
    python3 run_mixed_fpga.py
    python3 compare_precision_tracks.py

Every ratio is oriented so that ABOVE 1.00 MEANS THE MIXED CORE WINS, and below
1.00 means the baseline does. The header of each ratio column names the
direction explicitly, because the three quantities do not all improve in the
same direction:

    area ratio       = baseline logic LUTs / mixed logic LUTs
    frequency ratio  = mixed max frequency / baseline max frequency
    energy ratio     = baseline energy per transform / mixed energy per transform
    throughput ratio = mixed throughput / baseline throughput
    per-watt ratio   = mixed throughput per watt / baseline's
    per-area ratio   = mixed throughput per kLUT / baseline's

A ratio is printed only where both sides have the number. Energy is omitted for
any design whose switching activity was not annotated (saif_used = 0), so a
blank energy ratio means one of the two runs is untrustworthy, not that the
designs are equal -- check the validity table in that track's own report.

COMPARABILITY
  All three tracks go through the same unmodified vivado_synthesis_v2.tcl and
  generate_saif_funcsim.tcl, the same part, the same clock constraint, and the
  same register-array memory (Vivado infers BRAM). They differ in the activity
  testbench, because the three tops have different data widths; the stimulus
  pattern is the same in each.

  The cycle denominator must match across the three columns or the throughput
  comparison is meaningless. This script uses the compute-only count by default
  and --cycles e2e for end-to-end; it refuses to mix them.
"""

import argparse
import csv as csv_mod
import os
import re

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

TRACKS = [
    ("FP16", os.path.join(REPO_ROOT, "fp16_baseline", "synth", "fp16_fpga_metrics.csv"),
     os.path.join(REPO_ROOT, "fp16_baseline", "sim", "perf", "fp16_sqnr_results.txt")),
    ("FP32", os.path.join(REPO_ROOT, "fp32_baseline", "synth", "fp32_fpga_metrics.csv"),
     os.path.join(REPO_ROOT, "fp32_baseline", "sim", "perf", "fp32_sqnr_results.txt")),
    ("Mixed", os.path.join(REPO_ROOT, "synth_mixed", "mixed_fpga_metrics.csv"), None),
]
SELECTION_CSV = os.path.join(REPO_ROOT, "synth_mixed", "mixed_selected_chromosomes.csv")
DEFAULT_OUT = os.path.join(REPO_ROOT, "synth_mixed", "three_way_comparison.txt")

# N | exec | load | unload | e2e | avg sqnr | ...
SQNR_ROW = re.compile(r"^\s*(\d+)\s*\|\s*\d+\s*\|\s*-?\d+\s*\|\s*-?\d+\s*\|\s*-?\d+\s*\|"
                      r"\s*(-?[\d.]+)\s*\|")


def fnum(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def read_metrics(path):
    """{N: row} from a driver's metrics CSV."""
    if not os.path.isfile(path):
        return {}
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv_mod.DictReader(f):
            n = fnum(r.get("N"))
            if n:
                out[int(n)] = r
    return out


def read_sqnr(path):
    if not path or not os.path.isfile(path):
        return {}
    out = {}
    for line in open(path, encoding="utf-8", errors="replace"):
        m = SQNR_ROW.match(line)
        if m:
            out[int(m.group(1))] = float(m.group(2))
    return out


def read_selection():
    """Mixed SQNR and chromosome come from the selection table, not a metrics CSV."""
    out = {}
    if not os.path.isfile(SELECTION_CSV):
        return out
    with open(SELECTION_CSV, newline="", encoding="utf-8") as f:
        for r in csv_mod.DictReader(f):
            n = fnum(r.get("N"))
            if n:
                out[int(n)] = r
    return out


def val(row, key):
    return fnum(row.get(key)) if row else None


def energy_ok(row):
    """Energy is only meaningful where the SAIF actually annotated."""
    return row is not None and str(row.get("saif_used")) == "1"


def ratio(better_up, base):
    """better_up / base, or None if either side is missing or zero."""
    if better_up is None or base is None or base == 0:
        return None
    return better_up / base


def render(rows, cols, title, notes=()):
    labels = [c[0] for c in cols]
    units = [c[1] for c in cols]
    body = [[c[2](r) for c in cols] for r in rows]
    widths = [max(len(labels[i]), len(units[i]),
                  max((len(b[i]) for b in body), default=0))
              for i in range(len(cols))]
    L = ["", title,
         "  ".join(l.rjust(w) for l, w in zip(labels, widths)),
         "  ".join(u.rjust(w) for u, w in zip(units, widths)),
         "-" * (sum(widths) + 2 * (len(widths) - 1))]
    for b in body:
        L.append("  ".join(v.rjust(w) for v, w in zip(b, widths)))
    for n in notes:
        L.append("  " + n)
    return L


def main():
    ap = argparse.ArgumentParser(
        description="Join the FP16, FP32 and mixed FPGA metrics into one table")
    ap.add_argument("--cycles", choices=("compute", "e2e"), default="compute",
                    help="which cycle denominator to compare on; the same one is "
                         "used for all three tracks (default compute-only)")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--csv", default=None, help="also write the joined rows as CSV")
    args = ap.parse_args()

    data, sqnr, missing = {}, {}, []
    for name, mpath, spath in TRACKS:
        data[name] = read_metrics(mpath)
        sqnr[name] = read_sqnr(spath)
        if not data[name]:
            missing.append((name, mpath))
    sel = read_selection()
    for n, r in sel.items():
        s = fnum(r.get("sqnr_dB"))
        if s is not None:
            sqnr.setdefault("Mixed", {})[n] = s

    sizes = sorted(set().union(*[set(d) for d in data.values()]) if any(data.values()) else [])
    if not sizes:
        raise SystemExit("no metrics CSVs found; run the three drivers first")

    tag = args.cycles
    ck = "exec_cycles" if tag == "compute" else "e2e_cycles"

    def f(v, fmt="{:.2f}", dash="-"):
        return dash if v is None else fmt.format(v)

    rows = []
    for n in sizes:
        r16, r32, rmx = data["FP16"].get(n), data["FP32"].get(n), data["Mixed"].get(n)
        e = {k: (val(r, f"{tag}_energy_nj") if energy_ok(r) else None)
             for k, r in (("FP16", r16), ("FP32", r32), ("Mixed", rmx))}
        rows.append({
            "N": n,
            "r": {"FP16": r16, "FP32": r32, "Mixed": rmx},
            "lut": {k: val(r, "lut_count") for k, r in
                    (("FP16", r16), ("FP32", r32), ("Mixed", rmx))},
            "fmax": {k: val(r, "fmax_mhz") for k, r in
                     (("FP16", r16), ("FP32", r32), ("Mixed", rmx))},
            "pdyn": {k: val(r, "dynamic_power_w") for k, r in
                     (("FP16", r16), ("FP32", r32), ("Mixed", rmx))},
            "cyc": {k: val(r, ck) for k, r in
                    (("FP16", r16), ("FP32", r32), ("Mixed", rmx))},
            "thr": {k: val(r, f"{tag}_throughput_tps") for k, r in
                    (("FP16", r16), ("FP32", r32), ("Mixed", rmx))},
            "tpw": {k: val(r, f"{tag}_throughput_per_W_tpj") for k, r in
                    (("FP16", r16), ("FP32", r32), ("Mixed", rmx))},
            "tpa": {k: val(r, f"{tag}_throughput_per_klut") for k, r in
                    (("FP16", r16), ("FP32", r32), ("Mixed", rmx))},
            "energy": e,
            "sqnr": {k: sqnr.get(k, {}).get(n) for k in ("FP16", "FP32", "Mixed")},
            "chrom": (sel.get(n) or {}).get("chromosome", ""),
        })

    L = ["=" * 78,
         "FP16 vs FP32 vs MIXED FP4/FP8 - FPGA COMPARISON",
         "=" * 78, "",
         "Cycle denominator: %s" % ("compute only (start of transform to done)"
                                    if tag == "compute"
                                    else "end to end (load + compute + unload)"),
         "All three tracks: same Vivado scripts, same device, same clock constraint,",
         "same register-array memory. Ratios above 1.00 favour the mixed core.", ""]
    if missing:
        L.append("MISSING metrics for: " + ", ".join(f"{n} ({p})" for n, p in missing))
        L.append("")

    L += render(rows, [
        ("FFT size", "points", lambda r: str(r["N"])),
        ("Chromosome", "mixed core", lambda r: r["chrom"] or "-"),
        ("FP16 logic LUTs", "count", lambda r: f(r["lut"]["FP16"], "{:.0f}")),
        ("FP32 logic LUTs", "count", lambda r: f(r["lut"]["FP32"], "{:.0f}")),
        ("Mixed logic LUTs", "count", lambda r: f(r["lut"]["Mixed"], "{:.0f}")),
        ("Area ratio vs FP16", "x smaller for mixed",
         lambda r: f(ratio(r["lut"]["FP16"], r["lut"]["Mixed"]))),
        ("Area ratio vs FP32", "x smaller for mixed",
         lambda r: f(ratio(r["lut"]["FP32"], r["lut"]["Mixed"]))),
    ], "TABLE 1  AREA")

    L += render(rows, [
        ("FFT size", "points", lambda r: str(r["N"])),
        ("FP16 maximum frequency", "MHz", lambda r: f(r["fmax"]["FP16"])),
        ("FP32 maximum frequency", "MHz", lambda r: f(r["fmax"]["FP32"])),
        ("Mixed maximum frequency", "MHz", lambda r: f(r["fmax"]["Mixed"])),
        ("Frequency ratio vs FP16", "x faster for mixed",
         lambda r: f(ratio(r["fmax"]["Mixed"], r["fmax"]["FP16"]))),
        ("Frequency ratio vs FP32", "x faster for mixed",
         lambda r: f(ratio(r["fmax"]["Mixed"], r["fmax"]["FP32"]))),
    ], "TABLE 2  ACHIEVABLE CLOCK FREQUENCY")

    L += render(rows, [
        ("FFT size", "points", lambda r: str(r["N"])),
        ("FP16 cycles per transform", "clock cycles", lambda r: f(r["cyc"]["FP16"], "{:.0f}")),
        ("FP32 cycles per transform", "clock cycles", lambda r: f(r["cyc"]["FP32"], "{:.0f}")),
        ("Mixed cycles per transform", "clock cycles", lambda r: f(r["cyc"]["Mixed"], "{:.0f}")),
        ("FP16 throughput", "transforms per second", lambda r: f(r["thr"]["FP16"], "{:,.0f}")),
        ("FP32 throughput", "transforms per second", lambda r: f(r["thr"]["FP32"], "{:,.0f}")),
        ("Mixed throughput", "transforms per second", lambda r: f(r["thr"]["Mixed"], "{:,.0f}")),
        ("Throughput ratio vs FP16", "x faster for mixed",
         lambda r: f(ratio(r["thr"]["Mixed"], r["thr"]["FP16"]))),
        ("Throughput ratio vs FP32", "x faster for mixed",
         lambda r: f(ratio(r["thr"]["Mixed"], r["thr"]["FP32"]))),
    ], "TABLE 3  THROUGHPUT", notes=[
        "Cycle counts differ between tracks only through the pipeline depth of each",
        "butterfly, so most of any throughput gap comes from maximum frequency.",
    ])

    L += render(rows, [
        ("FFT size", "points", lambda r: str(r["N"])),
        ("FP16 dynamic power", "watts", lambda r: f(r["pdyn"]["FP16"], "{:.4f}")),
        ("FP32 dynamic power", "watts", lambda r: f(r["pdyn"]["FP32"], "{:.4f}")),
        ("Mixed dynamic power", "watts", lambda r: f(r["pdyn"]["Mixed"], "{:.4f}")),
        ("FP16 energy per transform", "nanojoules", lambda r: f(r["energy"]["FP16"])),
        ("FP32 energy per transform", "nanojoules", lambda r: f(r["energy"]["FP32"])),
        ("Mixed energy per transform", "nanojoules", lambda r: f(r["energy"]["Mixed"])),
        ("Energy ratio vs FP16", "x lower for mixed",
         lambda r: f(ratio(r["energy"]["FP16"], r["energy"]["Mixed"]))),
        ("Energy ratio vs FP32", "x lower for mixed",
         lambda r: f(ratio(r["energy"]["FP32"], r["energy"]["Mixed"]))),
    ], "TABLE 4  POWER AND ENERGY PER TRANSFORM", notes=[
        "Energy is blank wherever that track's switching activity was not annotated.",
        "It is never filled in from vectorless power, which barely varies by design.",
    ])

    L += render(rows, [
        ("FFT size", "points", lambda r: str(r["N"])),
        ("FP16 throughput per watt", "transforms per joule",
         lambda r: f(r["tpw"]["FP16"], "{:,.0f}")),
        ("FP32 throughput per watt", "transforms per joule",
         lambda r: f(r["tpw"]["FP32"], "{:,.0f}")),
        ("Mixed throughput per watt", "transforms per joule",
         lambda r: f(r["tpw"]["Mixed"], "{:,.0f}")),
        ("Per-watt ratio vs FP16", "x better for mixed",
         lambda r: f(ratio(r["tpw"]["Mixed"], r["tpw"]["FP16"]))),
        ("Per-watt ratio vs FP32", "x better for mixed",
         lambda r: f(ratio(r["tpw"]["Mixed"], r["tpw"]["FP32"]))),
    ], "TABLE 5  ENERGY EFFICIENCY", notes=[
        "Throughput per watt is 1 / energy per transform, so this table and the energy",
        "ratios above carry the same information in the units reviewers ask for.",
    ])

    L += render(rows, [
        ("FFT size", "points", lambda r: str(r["N"])),
        ("FP16 throughput per 1000 LUTs", "transforms per second",
         lambda r: f(r["tpa"]["FP16"], "{:,.0f}")),
        ("FP32 throughput per 1000 LUTs", "transforms per second",
         lambda r: f(r["tpa"]["FP32"], "{:,.0f}")),
        ("Mixed throughput per 1000 LUTs", "transforms per second",
         lambda r: f(r["tpa"]["Mixed"], "{:,.0f}")),
        ("Per-area ratio vs FP16", "x better for mixed",
         lambda r: f(ratio(r["tpa"]["Mixed"], r["tpa"]["FP16"]))),
        ("Per-area ratio vs FP32", "x better for mixed",
         lambda r: f(ratio(r["tpa"]["Mixed"], r["tpa"]["FP32"]))),
    ], "TABLE 6  AREA EFFICIENCY")

    L += render(rows, [
        ("FFT size", "points", lambda r: str(r["N"])),
        ("FP16 average SQNR", "dB", lambda r: f(r["sqnr"]["FP16"])),
        ("FP32 average SQNR", "dB", lambda r: f(r["sqnr"]["FP32"])),
        ("Mixed average SQNR", "dB", lambda r: f(r["sqnr"]["Mixed"])),
        ("Accuracy lost vs FP16", "dB", lambda r: f(
            None if r["sqnr"]["FP16"] is None or r["sqnr"]["Mixed"] is None
            else r["sqnr"]["FP16"] - r["sqnr"]["Mixed"])),
        ("Accuracy lost vs FP32", "dB", lambda r: f(
            None if r["sqnr"]["FP32"] is None or r["sqnr"]["Mixed"] is None
            else r["sqnr"]["FP32"] - r["sqnr"]["Mixed"])),
    ], "TABLE 7  ACCURACY - WHAT THE EFFICIENCY COSTS", notes=[
        "This is the column that decides whether the efficiency ratios above are a",
        "real win. The two baselines' SQNR is measured with the mixed evaluator's own",
        "methodology and signal set; the mixed figure is the sweep's, from the",
        "selection table. Both credit a bit-exact signal as 100 dB, so a baseline",
        "average near 100 dB means most signals were exact, not that the average is",
        "physically meaningful.",
    ])

    L += ["", "READING THE RATIOS",
          "  Above 1.00  the mixed core is better on that figure of merit.",
          "  Below 1.00  the baseline is better.",
          "  A dash      one of the two numbers is missing or untrustworthy; it is",
          "              never a 1.00 and never an assumption of equality."]

    text = "\n".join(L) + "\n"
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("\n" + text)
    print(f"[compare] table: {args.out}")

    if args.csv:
        flat = []
        for r in rows:
            d = {"N": r["N"], "chromosome": r["chrom"], "cycle_basis": tag}
            for field in ("lut", "fmax", "pdyn", "cyc", "thr", "tpw", "tpa",
                          "energy", "sqnr"):
                for k in ("FP16", "FP32", "Mixed"):
                    d[f"{field}_{k.lower()}"] = r[field][k]
            flat.append(d)
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv_mod.DictWriter(fh, fieldnames=list(flat[0].keys()))
            w.writeheader()
            w.writerows(flat)
        print(f"[compare] csv  : {args.csv}")


if __name__ == "__main__":
    main()
