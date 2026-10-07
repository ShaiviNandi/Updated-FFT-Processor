#!/usr/bin/env python3
"""Three-way FPGA comparison: FP16, FP32 and mixed FP4/FP8."""

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

SOURCE_NOTE = {
    "post-synth": (
        "Source: post-synthesis estimates (synth_design + opt_design; nothing "
        "placed or",
        "routed). Treat the absolute frequencies as estimates."),
    "post-route": (
        "Source: POST-ROUTE (placed, physically optimised and routed). Area, "
        "frequency and",
        "power are the routed figures; throughput and energy are recomputed "
        "from them at a %.1f ns clock."),
    "closing-period": (
        "Source: MEASURED CLOSING PERIODS. Area and power are the post-route "
        "figures; frequency is",
        "the period each design was measured to meet timing at, with positive "
        "slack. Energy per transform and throughput per watt are "
        "frequency-invariant and so are unchanged from post-route; throughput "
        "and throughput per LUT are recomputed at a %.1f ns clock."),
}

CLOSING_CSV = os.path.join(REPO_ROOT, "synth_mixed", "closing_period_all.csv")
# sweep_timing_constraint.py writes its track as the human label
CLOSING_TRACK = {"FP16 baseline": "FP16", "FP32 baseline": "FP32",
                 "Mixed FP4/FP8": "Mixed"}

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


def read_closing_periods(path):
    """Measured closing periods from sweep_timing_constraint.py's CSV.

    Returns {track: {N: row}}. A row whose bracketed flag is 0 is an upper
    bound, not a closing period: every probe met timing and the search ran out
    of budget, so the design is faster than the row says. Those are carried
    through with the flag intact and the caller decides; they are never
    silently treated as measurements.
    """
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv_mod.DictReader(f):
            track = CLOSING_TRACK.get((r.get("_track") or "").strip())
            n = fnum(r.get("N"))
            fmax = fnum(r.get("measured_fmax_mhz"))
            if track and n and fmax:
                out.setdefault(track, {})[int(n)] = {
                    "fmax_mhz": fmax,
                    "closing_period_ns": fnum(r.get("closing_period_ns")),
                    "slack_ns": fnum(r.get("slack_at_closing_ns")),
                    # absent column (a CSV from before the flag existed) is
                    # unknown, not confirmed -- treat it as unbracketed
                    "bracketed": str(r.get("bracketed", "")).strip() == "1",
                }
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
    ap.add_argument("--source",
                    choices=("post-synth", "post-route", "closing-period"),
                    default="post-synth",
                    help="post-synth (default) reads the *_fpga_metrics.csv "
                         "roll-ups. post-route reads the per-design "
                         "*_metrics_impl.csv that vivado_implement.tcl writes, "
                         "and recomputes every derived column from the routed "
                         "area, frequency and power. Use post-route whenever the "
                         "implementation delta differs by track: the tracks then "
                         "do not degrade alike and the post-synthesis ratios are "
                         "not comparable.")
    ap.add_argument("--closing-csv", default=CLOSING_CSV,
                    help="CSV from sweep_timing_constraint.py, read only by "
                         "--source closing-period")
    ap.add_argument("--clock-period", type=float, default=10.0,
                    help="clock period in ns used to recompute energy per "
                         "transform for --source post-route (default 10.0, "
                         "matching POWER_CLOCK_NS)")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--csv", default=None, help="also write the joined rows as CSV")
    args = ap.parse_args()

    import socket
    _host = re.sub(r"[^A-Za-z0-9_-]", "", socket.gethostname().split(".")[0]) or "host"
    log_path = os.path.splitext(os.path.abspath(args.out))[0] + f".{_host}.log"
    with _Tee(log_path):
        _run(args)


def _run(args):

    data, sqnr, missing = {}, {}, []
    for name, mpath, spath in TRACKS:
        if args.source in ("post-route", "closing-period"):
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

    if args.source in ("post-route", "closing-period"):
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

    closing_stats = {"measured": 0, "upper_bound": 0, "unmeasured": 0}
    if args.source == "closing-period":
        # Area and power stay post-route: the sweep's probes run --no-saif, so
        # they carry no usable power at all, and their area is the same netlist.
        # Only frequency is replaced, by the period at which the design was
        # measured to meet timing.
        #
        # Energy per transform is dynamic power x cycles x clock period, which
        # is frequency-invariant, so it carries over from the post-route run
        # unchanged -- and so does throughput per watt, being its reciprocal.
        # What changes is throughput (frequency / cycles) and throughput per
        # LUT. That is the whole of the difference this source makes.
        meas = read_closing_periods(args.closing_csv)
        for name in data:
            for n, row in data[name].items():
                m = (meas.get(name) or {}).get(n)
                if m is None:
                    # No measured period for this design. Blank the frequency
                    # rather than leaving the post-route estimate in place: a
                    # ratio of one track's measured frequency over another's
                    # extrapolated one looks like a result and is an artefact.
                    closing_stats["unmeasured"] += 1
                    for k in ("fmax_mhz", "crit_delay_ns",
                              "compute_throughput_tps", "e2e_throughput_tps",
                              "compute_throughput_per_klut",
                              "e2e_throughput_per_klut"):
                        row[k] = ""
                    continue
                closing_stats["measured" if m["bracketed"] else "upper_bound"] += 1
                row["fmax_mhz"] = m["fmax_mhz"]
                row["crit_delay_ns"] = m["closing_period_ns"]
                row["closing_slack_ns"] = m["slack_ns"]
                row["fmax_is_upper_bound"] = 0 if m["bracketed"] else 1
                for t, key in (("compute", "exec_cycles"),
                               ("e2e", "e2e_cycles")):
                    derive_from(row, fnum(row.get(key)), args.clock_period, t)

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
         "",
         SOURCE_NOTE[args.source][0],
         SOURCE_NOTE[args.source][1] % args.clock_period
         if "%" in SOURCE_NOTE[args.source][1] else SOURCE_NOTE[args.source][1],
         ""]
    if args.source == "closing-period":
        L += ["Frequency basis: %d design(s) measured and bracketed, "
              "%d upper bound(s), %d with no measured period (frequency and "
              "throughput left blank for those, never filled from an estimate)."
              % (closing_stats["measured"], closing_stats["upper_bound"],
                 closing_stats["unmeasured"]),
              ""]
        if closing_stats["upper_bound"]:
            L += ["An upper bound is a design where every probe met timing, so "
                  "its true frequency is HIGHER and its throughput ratio is "
                  "understated. See the closing-period CSV's bracketed column.",
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
    ], "TABLE 3  THROUGHPUT", notes=[])

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
    ], "TABLE 4  POWER AND ENERGY PER TRANSFORM", notes=[])

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
    ], "TABLE 5  ENERGY EFFICIENCY", notes=[])

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
    ], "TABLE 7  ACCURACY - WHAT THE EFFICIENCY COSTS", notes=[])


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
