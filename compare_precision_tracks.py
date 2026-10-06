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
import glob
import os
import sys
import re

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

TRACKS = [
    ("FP16", os.path.join(REPO_ROOT, "fp16_baseline", "synth", "fp16_fpga_metrics.csv"),
     os.path.join(REPO_ROOT, "fp16_baseline", "sim", "perf", "fp16_sqnr_results.txt")),
    ("FP32", os.path.join(REPO_ROOT, "fp32_baseline", "synth", "fp32_fpga_metrics.csv"),
     os.path.join(REPO_ROOT, "fp32_baseline", "sim", "perf", "fp32_sqnr_results.txt")),
    ("Mixed", os.path.join(REPO_ROOT, "synth_mixed", "mixed_fpga_metrics.csv"), None),
]
IMPL_DIRS = {
    "FP16":  os.path.join(REPO_ROOT, "fp16_baseline", "synth", "fpga_work_impl"),
    "FP32":  os.path.join(REPO_ROOT, "fp32_baseline", "synth", "fpga_work_impl"),
    "Mixed": os.path.join(REPO_ROOT, "synth_mixed", "fpga_work_impl"),
}
N_FROM_NAME = re.compile(r"_fft_?(\d+)(?:_|\b)")

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


def read_impl_metrics(track_dir):
    """{N: row} from the per-design *_metrics_impl.csv that vivado_implement.tcl
    writes, remapped onto the field names the tables below already use.

    The driver's own *_fpga_metrics_impl.csv roll-up is NOT the post-route data:
    build_report reads Vivado's <design>_metrics.csv, which v2 writes before
    place and route, so that roll-up holds post-SYNTHESIS numbers that merely
    happen to live under an _impl path. The post-route figures exist only in the
    per-design _impl CSVs, which is what this reads."""
    out = {}
    if not os.path.isdir(track_dir):
        return out
    for p in sorted(glob.glob(os.path.join(track_dir, "*_metrics_impl.csv"))):
        m = N_FROM_NAME.search(os.path.basename(p))
        if not m:
            continue
        d = {}
        with open(p, newline="", encoding="utf-8") as f:
            for r in csv_mod.reader(f):
                if len(r) == 2 and r[0] != "Metric":
                    d[r[0]] = r[1]
        if str(d.get("route_ran")) != "1":
            continue          # nothing to compare for a design that did not route
        out[int(m.group(1))] = {
            "N": m.group(1),
            "lut_count": d.get("impl_lut_count", ""),
            "fmax_mhz": d.get("impl_fmax_mhz", ""),
            "critical_path_delay_ns": d.get("impl_critical_path_delay_ns", ""),
            "dynamic_power_w": d.get("impl_dynamic_power_w", ""),
            "static_power_w": d.get("impl_static_power_w", ""),
            "saif_used": d.get("saif_used", ""),
            "synth_fmax_mhz": d.get("synth_fmax_mhz", ""),
            "synth_lut_count": d.get("synth_lut_count", ""),
            "synth_dynamic_power_w": d.get("synth_dynamic_power_w", ""),
        }
    return out


def derive_from(row, cycles, clock_ns, tag):
    """Throughput / energy / efficiency, recomputed for whichever power and
    frequency figures `row` carries. Post-route rows have no derived columns of
    their own, so they must be derived here rather than read."""
    def f(k):
        try:
            return float(row.get(k))
        except (TypeError, ValueError):
            return None
    if not cycles or cycles <= 0:
        return
    fmax, pdyn, luts = f("fmax_mhz"), f("dynamic_power_w"), f("lut_count")
    used = str(row.get("saif_used")) == "1"
    if fmax and fmax > 0:
        thr = fmax * 1e6 / cycles
        row[f"{tag}_throughput_tps"] = thr
        if luts and luts > 0:
            row[f"{tag}_throughput_per_klut"] = thr / (luts / 1000.0)
    if pdyn is not None and used:
        e = pdyn * cycles * clock_ns
        row[f"{tag}_energy_nj"] = e
        if e > 0:
            row[f"{tag}_throughput_per_W_tpj"] = 1e9 / e


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


def energy_ok(row, min_coverage=0.10, min_power_shift=0.05):
    """Energy is only meaningful where the switching activity really annotated.

    Evaluated here rather than trusted from the row, because a roll-up CSV
    carries whichever verdict the driver reached when it last ran, and a driver
    run that predates a change to the gate leaves a stale 0 behind. Both inputs
    are columns of the roll-up, so the test is reproducible from the file itself
    and the comparison cannot silently inherit an out-of-date judgement.

    Accepts on EITHER test: annotated-net coverage, or dynamic power having moved
    away from the vectorless baseline. See the drivers for why neither alone is
    sufficient."""
    if row is None:
        return False
    def f(k):
        try:
            return float(row.get(k))
        except (TypeError, ValueError):
            return None
    cov = f("saif_coverage")
    pdyn, pvl = f("dynamic_power_w"), f("dynamic_power_vectorless_w")
    shift = (abs(pdyn - pvl) / abs(pvl)
             if (pdyn is not None and pvl not in (None, 0.0)) else None)
    if cov is None and shift is None:
        return str(row.get("saif_used")) == "1"     # nothing to re-test with
    return bool((cov is not None and cov >= min_coverage)
                or (shift is not None and shift >= min_power_shift))


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


# ---------------------------------------------------------------------------
class _Tee:
    """Duplicate everything printed to a log file beside the report.

    Terminal scrollback is not a record: a run whose output is only on screen is
    lost the moment the window closes, and several results in this project were.
    Every script writes its own console log next to its outputs so the full run,
    warnings included, survives without anyone having to copy text out.
    """

    def __init__(self, path):
        self.path = path
        self.stream = None
        self.stdout = None

    def __enter__(self):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            self.stream = open(self.path, "w", encoding="utf-8")
        except OSError:
            return self          # logging must never break the run
        self.stdout = sys.stdout
        sys.stdout = self
        return self

    def __exit__(self, *exc):
        if self.stdout is not None:
            sys.stdout = self.stdout
        if self.stream is not None:
            try:
                self.stream.write(f"\n[log written to {self.path}]\n")
                self.stream.close()
            except OSError:
                pass
        return False

    def write(self, s):
        if self.stdout is not None:
            self.stdout.write(s)
        if self.stream is not None:
            try:
                self.stream.write(s)
            except OSError:
                pass

    def flush(self):
        for t in (self.stdout, self.stream):
            try:
                if t is not None:
                    t.flush()
            except OSError:
                pass



def main():
    ap = argparse.ArgumentParser(
        description="Join the FP16, FP32 and mixed FPGA metrics into one table")
    ap.add_argument("--cycles", choices=("compute", "e2e"), default="compute",
                    help="which cycle denominator to compare on; the same one is "
                         "used for all three tracks (default compute-only)")
    ap.add_argument("--source", choices=("post-synth", "post-route"),
                    default="post-synth",
                    help="post-synth (default) reads the *_fpga_metrics.csv "
                         "roll-ups. post-route reads the per-design "
                         "*_metrics_impl.csv that vivado_implement.tcl writes, "
                         "and recomputes every derived column from the routed "
                         "area, frequency and power. Use post-route whenever the "
                         "implementation delta differs by track: the tracks then "
                         "do not degrade alike and the post-synthesis ratios are "
                         "not comparable.")
    ap.add_argument("--clock-period", type=float, default=10.0,
                    help="clock period in ns used to recompute energy per "
                         "transform for --source post-route (default 10.0, "
                         "matching POWER_CLOCK_NS)")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--csv", default=None, help="also write the joined rows as CSV")
    args = ap.parse_args()

    # Hostname in the filename, deliberately: these logs are committed so that
    # digital-1's results can be read from any machine, and two machines writing
    # the same path is what turned a pull into an add/add merge conflict.
    import socket
    _host = re.sub(r"[^A-Za-z0-9_-]", "", socket.gethostname().split(".")[0]) or "host"
    log_path = os.path.splitext(os.path.abspath(args.out))[0] + f".{_host}.log"
    with _Tee(log_path):
        _run(args)


def _run(args):

    data, sqnr, missing = {}, {}, []
    for name, mpath, spath in TRACKS:
        if args.source == "post-route":
            src = IMPL_DIRS.get(name, "")
            data[name] = read_impl_metrics(src)
            if not data[name]:
                missing.append((name, src))
        else:
            data[name] = read_metrics(mpath)
            if not data[name]:
                missing.append((name, mpath))
        sqnr[name] = read_sqnr(spath)
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

    # Post-route rows hold routed area, frequency and power but none of the
    # derived columns, so recompute those here from the same cycle counts the
    # post-synthesis path uses. Cycles are a property of the FSM and do not
    # change with implementation.
    if args.source == "post-route":
        cyc_src = {}
        for name, mpath, _ in TRACKS:
            for n, row in read_metrics(mpath).items():
                cyc_src.setdefault(name, {})[n] = row
        for name in data:
            for n, row in data[name].items():
                base = (cyc_src.get(name) or {}).get(n, {})
                for t, key in (("compute", "exec_cycles"), ("e2e", "e2e_cycles")):
                    row.setdefault(key, base.get(key, ""))
                    derive_from(row, fnum(row.get(key)), args.clock_period, t)

    # A post-synthesis roll-up written before the gate changed has blank energy
    # columns for rows the gate now accepts. Both powers and the cycle counts are
    # in the roll-up, so recompute rather than asking for a driver re-run.
    if args.source == "post-synth":
        for name in data:
            for row in data[name].values():
                if not energy_ok(row):
                    continue
                for t, key in (("compute", "exec_cycles"), ("e2e", "e2e_cycles")):
                    if not row.get(f"{t}_energy_nj"):
                        row["saif_used"] = 1
                        derive_from(row, fnum(row.get(key)), args.clock_period, t)

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
         "same register-array memory. Ratios above 1.00 favour the mixed core.",
         "",
         ("Source: POST-ROUTE (placed, physically optimised and routed). Area, "
          "frequency and" if args.source == "post-route" else
          "Source: post-synthesis estimates (synth_design + opt_design; nothing "
          "placed or"),
         ("power are the routed figures; throughput and energy are recomputed "
          "from them at a %.1f ns clock." % args.clock_period
          if args.source == "post-route" else
          "routed). Treat the absolute frequencies as estimates."),
         ""]
    if missing:
        L.append("MISSING metrics for: " + ", ".join(f"{n} ({p})" for n, p in missing))
        L.append("")

    L += render(rows, [
        ("FFT size", "points", lambda r: str(r["N"])),
        ("Chromosome", "one bit per stage", lambda r: r["chrom"] or "-"),
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
            d = {"N": r["N"], "chromosome": r["chrom"], "cycle_basis": tag,
             "source": args.source}
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
