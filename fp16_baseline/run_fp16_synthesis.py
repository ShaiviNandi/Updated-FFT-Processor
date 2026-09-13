#!/usr/bin/env python3
"""
Batch Vivado synthesis driver for the FP16 baseline FFT cores.

Runs the same out-of-context flow, same FPGA part, same clock period and the
same report-extraction logic that objectiveEvaluationFFT.py uses for the
mixed-precision cores -- so the numbers land in the same CSV schema and are
directly comparable.

With --with-mixed it also re-synthesises the matching NSGA-optimised
mixed-precision core in the SAME invocation, through the EXISTING and
unmodified ../vivado_synthesis.tcl. That is the controlled comparison worth
putting in the paper: one Vivado version, one part, one clock, one session.

Run from the repository root (C:\\Updated-FFT-Processor or its Linux
equivalent), the same directory you run runMixedFFTOptimization.py from:

    python3 fp16_baseline/run_fp16_synthesis.py --with-mixed

Common variants:

    # just one size, quick smoke test
    python3 fp16_baseline/run_fp16_synthesis.py --sizes 256

    # FP16 only, all sizes, custom clock
    python3 fp16_baseline/run_fp16_synthesis.py --clock 80.0

    # re-tabulate without re-running Vivado
    python3 fp16_baseline/run_fp16_synthesis.py --report-only --with-mixed
"""

import argparse
import csv
import glob
import os
import re
import subprocess
import sys
import time

ALL_SIZES = [2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# Metric rows written by both TCL scripts, in CSV order
METRICS = [
    "lut_count", "lutram_count", "dsp_count", "bram_count", "ff_count",
    "total_power_w", "wns_ns", "critical_path_delay_ns", "clock_period_ns",
]


# ---------------------------------------------------------------------------
# Configuration: read the project's own globals rather than duplicating them
# ---------------------------------------------------------------------------
def read_globals():
    """Scrape VIVADO_PATH / CLOCK_PERIOD / FPGA_DEVICE without importing
    globalVariablesMixedFFT (importing it creates directories and writes to
    optimization.log as a side effect)."""
    defaults = {
        "vivado": "/home/digital-1/2025.2/Vivado/bin/vivado",
        "clock": 80.0,
        "part": "xc7a35tcpg236-1",
    }
    path = os.path.join(REPO, "globalVariablesMixedFFT.py")
    if not os.path.exists(path):
        return defaults
    text = open(path, encoding="utf-8", errors="replace").read()
    m = re.search(r"^VIVADO_PATH\s*=\s*['\"](.+?)['\"]", text, re.M)
    if m:
        defaults["vivado"] = m.group(1)
    m = re.search(r"^CLOCK_PERIOD\s*=\s*([0-9.]+)", text, re.M)
    if m:
        defaults["clock"] = float(m.group(1))
    m = re.search(r"^FPGA_DEVICE\s*=\s*['\"](.+?)['\"]", text, re.M)
    if m:
        defaults["part"] = m.group(1)
    return defaults


def find_mixed_core(n):
    """Locate the optimal mixed-precision core for size n in generated_cores/."""
    pattern = os.path.join(REPO, "generated_cores", f"fft_{n}_sol_*")
    for d in sorted(glob.glob(pattern)):
        core = os.path.join(d, f"mixed_fft_{n}_core.v")
        top = os.path.join(d, f"mixed_fft_{n}_top.v")
        if os.path.exists(core) and os.path.exists(top):
            return core, top
    return None, None


# ---------------------------------------------------------------------------
# Vivado invocation
# ---------------------------------------------------------------------------
def run_vivado(cmd, log_path, timeout):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, cwd=REPO)
    except subprocess.TimeoutExpired:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"TIMEOUT after {timeout}s\ncmd: {' '.join(cmd)}\n")
        return False, timeout
    dt = time.time() - t0
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"cmd: {' '.join(cmd)}\nreturncode: {r.returncode}\n")
        f.write("\n===== STDOUT =====\n")
        f.write(r.stdout or "")
        f.write("\n===== STDERR =====\n")
        f.write(r.stderr or "")
    if r.returncode != 0:
        errs = [ln for ln in (r.stdout or "").splitlines()
                if "ERROR" in ln or "CRITICAL WARNING" in ln]
        for ln in errs[:15]:
            print(f"      {ln.strip()}")
    return r.returncode == 0, dt


def synth_fp16(n, cfg, args):
    design = f"fp16_fft_{n}"
    core = os.path.join(HERE, "generated_cores", design, f"{design}_core.v")
    top = os.path.join(HERE, "generated_cores", design, f"{design}_top.v")
    if not (os.path.exists(core) and os.path.exists(top)):
        print(f"  [fp16 N={n}] SKIP - RTL not found; run fp16_template_generator.py")
        return None

    csv_out = os.path.join(args.reports, f"{design}_metrics.csv")
    cmd = [
        cfg["vivado"], "-mode", "batch",
        "-source", os.path.join(HERE, "vivado_synthesis_fp16.tcl"),
        "-nojournal", "-log", os.path.join(args.reports, f"{design}_vivado.log"),
        "-tclargs", design, os.path.abspath(csv_out), str(args.clock),
        os.path.abspath(core), os.path.abspath(top),
        os.path.abspath(os.path.join(HERE, "verilog_sources")),
        args.part,
        os.path.abspath(os.path.join(REPO, "verilog_sources")),
        os.path.abspath(os.path.join(HERE, "verilog_sources",
                                     "twiddles_fp16_1024.txt")),
    ]
    ok, dt = run_vivado(cmd, os.path.join(args.reports, f"{design}_run.log"),
                        args.timeout)
    print(f"  [fp16 N={n}] {'ok' if ok else 'FAILED'}  ({dt:.0f}s)")
    return csv_out if ok else None


def synth_mixed(n, cfg, args):
    core, top = find_mixed_core(n)
    if not core:
        print(f"  [mixed N={n}] SKIP - no generated_cores/fft_{n}_sol_* found")
        return None

    design = f"mixed_fft_{n}"
    csv_out = os.path.join(args.reports, f"{design}_metrics.csv")
    # NOTE: the existing vivado_synthesis.tcl is used unmodified.
    cmd = [
        cfg["vivado"], "-mode", "batch",
        "-source", os.path.join(REPO, "vivado_synthesis.tcl"),
        "-nojournal", "-log", os.path.join(args.reports, f"{design}_vivado.log"),
        "-tclargs", design, os.path.abspath(csv_out), str(args.clock),
        os.path.abspath(core), os.path.abspath(top),
        os.path.abspath(os.path.join(REPO, "verilog_sources")),
        args.part,
    ]
    ok, dt = run_vivado(cmd, os.path.join(args.reports, f"{design}_run.log"),
                        args.timeout)
    print(f"  [mixed N={n}] {'ok' if ok else 'FAILED'}  ({dt:.0f}s)")
    return csv_out if ok else None


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def load_metrics(path):
    if not path or not os.path.exists(path):
        return None
    d = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) == 2 and row[0] != "Metric":
                d[row[0]] = row[1]
    return d


def num(d, key):
    try:
        return float(d[key])
    except (KeyError, TypeError, ValueError):
        return None


def ratio(a, b):
    if a is None or b is None or b == 0:
        return ""
    return f"{a / b:.2f}x"


def build_report(sizes, args):
    rows = []
    for n in sizes:
        f16 = load_metrics(os.path.join(args.reports, f"fp16_fft_{n}_metrics.csv"))
        mix = load_metrics(os.path.join(args.reports, f"mixed_fft_{n}_metrics.csv"))
        if f16 is None and mix is None:
            continue
        row = {"N": n}
        for tag, d in (("fp16", f16), ("mixed", mix)):
            for m in METRICS:
                row[f"{tag}_{m}"] = (d or {}).get(m, "")
        for m in ("lut_count", "dsp_count", "bram_count", "ff_count",
                  "total_power_w", "critical_path_delay_ns"):
            row[f"ratio_{m}"] = ratio(num(f16, m), num(mix, m))
        rows.append(row)

    if not rows:
        print("\nNo metrics CSVs found. Nothing to tabulate.")
        return

    out = os.path.join(args.reports, "fp16_vs_mixed_comparison.csv")
    fields = list(rows[0].keys())
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    hdr = (f"{'N':>5} | {'LUT':>7} {'DSP':>5} {'BRAM':>5} {'FF':>7} "
           f"{'Pwr(W)':>8} {'Crit(ns)':>9}")
    print("\n" + "=" * 78)
    print("FP16 baseline")
    print(hdr)
    print("-" * 78)
    for r in rows:
        print(f"{r['N']:>5} | {r['fp16_lut_count']:>7} {r['fp16_dsp_count']:>5} "
              f"{r['fp16_bram_count']:>5} {r['fp16_ff_count']:>7} "
              f"{r['fp16_total_power_w']:>8} {r['fp16_critical_path_delay_ns']:>9}")

    if any(r["mixed_lut_count"] for r in rows):
        print("\nMixed-precision (FP4/FP8) optimal cores")
        print(hdr)
        print("-" * 78)
        for r in rows:
            print(f"{r['N']:>5} | {r['mixed_lut_count']:>7} {r['mixed_dsp_count']:>5} "
                  f"{r['mixed_bram_count']:>5} {r['mixed_ff_count']:>7} "
                  f"{r['mixed_total_power_w']:>8} {r['mixed_critical_path_delay_ns']:>9}")

        print("\nFP16 / mixed ratio  (>1 means FP16 costs more)")
        print(hdr)
        print("-" * 78)
        for r in rows:
            print(f"{r['N']:>5} | {r['ratio_lut_count']:>7} {r['ratio_dsp_count']:>5} "
                  f"{r['ratio_bram_count']:>5} {r['ratio_ff_count']:>7} "
                  f"{r['ratio_total_power_w']:>8} "
                  f"{r['ratio_critical_path_delay_ns']:>9}")

    print("=" * 78)
    print(f"\nWrote {out}")
    print("Cycle counts are identical by construction (measured: 1123 cycles at "
          "N=256 for both), so latency differences come only from Crit(ns).")


# ---------------------------------------------------------------------------
def main():
    cfg = read_globals()
    ap = argparse.ArgumentParser(
        description="Batch Vivado synthesis for the FP16 baseline FFT cores.")
    ap.add_argument("--sizes", type=int, nargs="*", default=ALL_SIZES,
                    help="FFT sizes to synthesise (default: all 2..1024)")
    ap.add_argument("--with-mixed", action="store_true",
                    help="also synthesise the matching mixed-precision cores "
                         "in the same session, via the unmodified "
                         "vivado_synthesis.tcl")
    ap.add_argument("--clock", type=float, default=cfg["clock"],
                    help=f"clock period in ns (default {cfg['clock']} from "
                         f"globalVariablesMixedFFT.py)")
    ap.add_argument("--part", type=str, default=cfg["part"],
                    help=f"FPGA part (default {cfg['part']})")
    ap.add_argument("--vivado", type=str, default=cfg["vivado"],
                    help=f"path to the vivado binary (default {cfg['vivado']})")
    ap.add_argument("--reports", type=str,
                    default=os.path.join(REPO, "reports", "fp16_baseline"),
                    help="directory for metrics CSVs and logs")
    ap.add_argument("--timeout", type=int, default=1800,
                    help="per-design Vivado timeout in seconds (default 1800)")
    ap.add_argument("--report-only", action="store_true",
                    help="skip synthesis, just re-tabulate existing CSVs")
    args = ap.parse_args()

    cfg["vivado"] = args.vivado
    os.makedirs(args.reports, exist_ok=True)

    for n in args.sizes:
        if n & (n - 1) or n < 2 or n > 1024:
            sys.exit(f"{n} is not a supported power of two in [2, 1024]")

    if not args.report_only:
        if not os.path.exists(args.vivado):
            sys.exit(f"vivado not found at {args.vivado}\n"
                     f"Pass --vivado /path/to/vivado")
        print(f"part   : {args.part}")
        print(f"clock  : {args.clock} ns")
        print(f"vivado : {args.vivado}")
        print(f"reports: {args.reports}")
        print(f"sizes  : {args.sizes}\n")

        for n in args.sizes:
            synth_fp16(n, cfg, args)
            if args.with_mixed:
                synth_mixed(n, cfg, args)

    build_report(args.sizes, args)


if __name__ == "__main__":
    main()
