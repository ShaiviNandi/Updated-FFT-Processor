#!/usr/bin/env python3
"""
Mixed-Precision FFT  --  FPGA PPA + Throughput, gated wrapper
=============================================================
The third row of the paper's comparison table, produced by exactly the flow the
two baselines use: `vivado_synthesis_v2.tcl` + `generate_saif_funcsim.tcl`,
unmodified, with `tb/tb_fft_power.v` and SAIF_STRIP_PATH='tb_fft_power/uut'.

WHY THIS EXISTS RATHER THAN REUSING generated_cores/
  The cores in `generated_cores/` cannot be the mixed row:

    1. They instantiate the UNGATED `butterfly_wrapper`. The current
       `fft_template_generator.py` instantiates
       `butterfly_wrapper_gated #(.PIPELINE_OPERANDS(0))`. In the ungated
       wrapper both the FP4 and the FP8 datapath switch every cycle and the
       result is muxed, so dynamic power is constant by construction and the
       per-chromosome precision schedule is invisible to report_power. Every
       energy number in the 2026-09-24 sweep comes from the gated wrapper;
       measuring the ungated cores would put a different architecture in the
       comparison than the one the paper optimises.
    2. Their chromosomes come from `best_chromosomes.csv`, dated 2026-07-06 and
       selected from the UNFIXED `all_solutions_fft<N>.csv`, whose sqnr_dB
       column is the mirrored-parabola artefact that `fix_sqnr.py` documents and
       repairs. A selection whose SQNR term is wrong is not the balanced
       optimum.

  So this driver re-selects from `all_solutions_fft<N>_fixed.csv`, regenerates
  with the current (gated) generator into a SEPARATE directory, and leaves
  `generated_cores/` untouched.

SELECTION  (identical arithmetic to optimal_designs.py)
  Per size, over the solutions that satisfy sqnr_dB >= --sqnr-floor and
  meets_timing == 1, preferring the truly mixed subset (a chromosome containing
  both a 0 and a 1) and falling back to the whole filtered set if none is mixed:

      norm_x       = (x - min) / (max - min)          over that same subset
                     (0.5 if max == min)
      balance_score = sqrt(  norm_power_W**2
                           + norm_area_LUTs**2
                           + norm_crit_delay_ns**2
                           + (1 - norm_sqnr_dB)**2 )

  i.e. Euclidean distance to the ideal corner (min power, min area, min delay,
  max SQNR) in the min-max normalised objective box; lowest wins. Note that the
  normalisation is per-size and over the filtered set, so balance_score is a
  within-size ranking, never comparable across sizes.

  This is deliberately re-implemented in the standard library rather than pandas
  so the driver has no third-party dependency on the synthesis host. Run with
  --verify-selection to re-derive the same table with pandas exactly as
  optimal_designs.py does and assert the chromosomes agree.

CYCLES
  ExecCycles comes from the solutions CSV (`avg_exec_cycles`) -- the same
  compute-only, start-to-done count the baselines report. The gated wrapper at
  PIPELINE_OPERANDS=0 is pure combinational operand isolation with no added
  register stage, so it cannot change the cycle count; at PIPELINE_OPERANDS=1 it
  would, and this driver refuses that parameter for exactly that reason.

  End-to-end cycles are ExecCycles + 4N + 1. Load is N+1 cycles and unload is 3N
  cycles, read off the load/unload loops in performance_evaluator.py's generated
  testbench (one posedge per sample plus a trailing one; three posedges per
  sample), which is also what the FP16 and FP32 evaluators measure directly
  (N=8: 9 and 24; N=64: 65 and 192). Pass --cycles-file to override with a
  measured table instead.

Usage (from the repository root):
    python3 run_mixed_fpga.py --select-only                  # just the table
    python3 run_mixed_fpga.py --sizes 256
    python3 run_mixed_fpga.py                                # all 10
    python3 run_mixed_fpga.py --use-dsp 0                    # LUT-only area
    python3 run_mixed_fpga.py --report-only                  # re-tabulate

Outputs:
    synth_mixed/mixed_selected_chromosomes.csv   the selection table
    synth_mixed/mixed_fpga_report.txt            the PPA + throughput tables
    synth_mixed/mixed_fpga_metrics.csv           one row per size
    generated_cores_gated/<design>/              the regenerated RTL
    synth_mixed/fpga_work/<design>/              per-design Vivado CSVs + logs

STATUS: NOT RUN AGAINST VIVADO. The selection arithmetic and the report
  rendering are exercised; the two Vivado passes are the same invocations
  objectiveEvaluationFFT.py makes, but this driver's own command construction
  has not been executed against a real install.
"""

import argparse
import csv as csv_mod
import math
import os
import re
import shutil
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

ALL_SIZES = [2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]

# The repo's own scripts -- used unmodified.
V2_TCL = os.path.join(REPO_ROOT, "vivado_synthesis_v2.tcl")
SAIF_TCL = os.path.join(REPO_ROOT, "generate_saif_funcsim.tcl")

DEFAULT_RESULTS = os.path.join(REPO_ROOT, "results")
DEFAULT_VERILOG = os.path.join(REPO_ROOT, "verilog_sources")
DEFAULT_GENERATED = os.path.join(REPO_ROOT, "generated_cores_gated")
DEFAULT_TB = os.path.join(REPO_ROOT, "tb", "tb_fft_power.v")
OUT_DIR = os.path.join(REPO_ROOT, "synth_mixed")
DEFAULT_WORK = os.path.join(OUT_DIR, "fpga_work")
DEFAULT_OUT = os.path.join(OUT_DIR, "mixed_fpga_report.txt")
DEFAULT_CSV = os.path.join(OUT_DIR, "mixed_fpga_metrics.csv")
DEFAULT_SEL = os.path.join(OUT_DIR, "mixed_selected_chromosomes.csv")

CHECKSUM_RE = re.compile(r"Synth Design complete\s*\|\s*Checksum:\s*(\S+)")
SAIF_NETS_RE = re.compile(r"Design nets matched\s*=\s*(\d+)\s+of\s+(\d+)")
GENE_RE = re.compile(r"^s(\d+)_(mult|add)$")

MIN_OBJECTIVES = ["power_W", "area_LUTs", "crit_delay_ns"]
MAX_OBJECTIVE = "sqnr_dB"


def log(msg):
    print(f"[mixed-fpga] {msg}", flush=True)


def saif_coverage(text):
    """(matched, total) from a power-pass log, or None."""
    m = SAIF_NETS_RE.search(text or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def checksum_of(text):
    m = CHECKSUM_RE.search(text or "")
    return m.group(1) if m else None


def reclaim_saif_workdir(design):
    """generate_saif_funcsim.tcl leaves /tmp/fsaif_<design>/ holding a funcsim
    netlist and an xsim snapshot. Best effort: a sweep must never die because
    scratch could not be removed."""
    work = os.path.join("/tmp", f"fsaif_{design}")
    if not os.path.isdir(work):
        return
    try:
        shutil.rmtree(work)
    except OSError as e:
        log(f"  could not reclaim {work}: {e}")


def read_globals():
    """Scrape the project's own globals rather than duplicating them. Text-scrape,
    not import: importing globalVariablesMixedFFT creates directories and appends
    to optimization.log."""
    d = {"vivado": "/home/digital-1/2025.2/Vivado/bin/vivado",
         "clock": 10.0, "part": "xc7a35tcpg236-1",
         "frames": 4, "use_dsp": 1, "min_cov": 0.10,
         "timeout": 1800, "clean_saif": True,
         "strip": "tb_fft_power/uut"}
    p = os.path.join(REPO_ROOT, "globalVariablesMixedFFT.py")
    if not os.path.exists(p):
        return d
    t = open(p, encoding="utf-8", errors="replace").read()
    for key, pat, cast in (
            ("vivado",  r"^VIVADO_PATH\s*=\s*['\"](.+?)['\"]", str),
            # POWER_CLOCK_NS is the power/XDC clock; CLOCK_PERIOD is only the
            # latency normaliser (claude/saif-power-flow-working.md).
            ("clock",   r"^POWER_CLOCK_NS\s*=\s*([0-9.]+)", float),
            ("part",    r"^FPGA_DEVICE\s*=\s*['\"](.+?)['\"]", str),
            ("strip",   r"^SAIF_STRIP_PATH\s*=\s*['\"](.+?)['\"]", str),
            ("frames",  r"^SAIF_FRAMES\s*=\s*(\d+)", int),
            ("use_dsp", r"^USE_DSP\s*=\s*(\d+)", int),
            ("min_cov", r"^SAIF_MIN_COVERAGE\s*=\s*([0-9.]+)", float),
            ("timeout", r"^VIVADO_TIMEOUT_S\s*=\s*(\d+)", int)):
        m = re.search(pat, t, re.M)
        if m:
            d[key] = cast(m.group(1))
    m = re.search(r"^CLEAN_SAIF_WORKDIRS\s*=\s*(True|False)", t, re.M)
    if m:
        d["clean_saif"] = (m.group(1) == "True")
    return d


# ---------------------------------------------------------------------------
# Selection: optimal_designs.py's balance_score, standard library only
# ---------------------------------------------------------------------------
def gene_columns(fieldnames):
    """s<stage>_mult / s<stage>_add, ordered by stage then mult-before-add --
    the same key optimal_designs.py sorts on, so the chromosome bit order is
    identical to the one the generator expects."""
    cols = [c for c in (fieldnames or []) if GENE_RE.match(c)]
    return sorted(cols, key=lambda c: (int(GENE_RE.match(c).group(1)),
                                       0 if GENE_RE.match(c).group(2) == "mult" else 1))


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def select_best(n, results_dir, suffix, sqnr_floor):
    """Return (best_row_dict, diagnostics) or (None, diagnostics)."""
    path = os.path.join(results_dir, f"fft_{n}", f"all_solutions_fft{n}{suffix}.csv")
    diag = {"N": n, "csv": path, "rows_total": 0, "rows_passing": 0,
            "rows_mixed": 0, "config_type": "", "note": ""}
    if not os.path.isfile(path):
        diag["note"] = "solutions CSV not found"
        return None, diag

    with open(path, newline="", encoding="utf-8") as f:
        rd = csv_mod.DictReader(f)
        genes = gene_columns(rd.fieldnames)
        rows = list(rd)
    diag["rows_total"] = len(rows)
    diag["gene_columns"] = len(genes)

    missing = [c for c in MIN_OBJECTIVES + [MAX_OBJECTIVE] if c not in (rows[0] if rows else {})]
    if missing:
        diag["note"] = f"missing columns {missing}"
        return None, diag
    if not genes:
        diag["note"] = "no s<k>_mult / s<k>_add columns"
        return None, diag

    # Filter: SQNR floor, and timing if the column exists.
    passing = []
    for r in rows:
        s = fnum(r.get(MAX_OBJECTIVE))
        if s is None or s < sqnr_floor:
            continue
        if "meets_timing" in r:
            mt = fnum(r.get("meets_timing"))
            if mt is None or int(mt) != 1:
                continue
        if any(fnum(r.get(c)) is None for c in MIN_OBJECTIVES):
            continue
        passing.append(r)
    diag["rows_passing"] = len(passing)
    if not passing:
        diag["note"] = f"no solution meets SQNR >= {sqnr_floor} dB and timing"
        return None, diag

    def bits(r):
        return [int(float(r[c])) for c in genes]

    mixed = [r for r in passing if 0 in bits(r) and 1 in bits(r)]
    diag["rows_mixed"] = len(mixed)
    target = mixed if mixed else passing
    diag["config_type"] = "Mixed Precision" if mixed else "Fallback"
    if not mixed:
        diag["note"] = ("no truly mixed chromosome passes the filters; falling back "
                        "to the whole filtered set (this row is uniform precision)")

    # Min-max normalise over `target`, exactly as optimal_designs.py does.
    norm = {}
    for c in MIN_OBJECTIVES + [MAX_OBJECTIVE]:
        vals = [fnum(r[c]) for r in target]
        lo, hi = min(vals), max(vals)
        norm[c] = [0.5] * len(target) if lo == hi else [(v - lo) / (hi - lo) for v in vals]

    best_i, best_score = None, None
    for i in range(len(target)):
        score = math.sqrt(
            norm["power_W"][i] ** 2
            + norm["area_LUTs"][i] ** 2
            + norm["crit_delay_ns"][i] ** 2
            + (1.0 - norm[MAX_OBJECTIVE][i]) ** 2)
        if best_score is None or score < best_score:
            best_i, best_score = i, score

    r = dict(target[best_i])
    out = {
        "N": n,
        "solution_id": int(float(r.get("solution_id", -1))),
        "chromosome": "".join(str(b) for b in bits(r)),
        "config_type": diag["config_type"],
        "balance_score": round(best_score, 6),
        "power_W": fnum(r.get("power_W")),
        "area_LUTs": fnum(r.get("area_LUTs")),
        "crit_delay_ns": fnum(r.get("crit_delay_ns")),
        "sqnr_dB": fnum(r.get(MAX_OBJECTIVE)),
        "sqnr_dB_asreported": fnum(r.get("sqnr_dB_asreported")),
        "energy_pJ": fnum(r.get("energy_pJ")),
        "exec_cycles": int(float(r["avg_exec_cycles"])) if fnum(r.get("avg_exec_cycles")) else None,
        "candidates": len(target),
    }
    if out["exec_cycles"]:
        # load = N+1, unload = 3N (performance_evaluator.py's generated TB)
        out["e2e_cycles"] = out["exec_cycles"] + 4 * n + 1
    else:
        out["e2e_cycles"] = None
    return out, diag


def verify_selection_with_pandas(sel, results_dir, suffix, sqnr_floor):
    """Re-derive the same table the way optimal_designs.py does and compare, so a
    divergence between this driver's arithmetic and the repo's own selector is a
    loud failure rather than a silently different paper row."""
    try:
        import pandas as pd
        import numpy as np
    except ImportError:
        log("--verify-selection needs pandas/numpy; skipping the cross-check")
        return True
    ok = True
    for row in sel:
        n = row["N"]
        df = pd.read_csv(os.path.join(results_dir, f"fft_{n}",
                                      f"all_solutions_fft{n}{suffix}.csv"))
        gc = sorted([c for c in df.columns if GENE_RE.match(c)],
                    key=lambda x: (int(x.split("_")[0][1:]),
                                   0 if x.split("_")[1] == "mult" else 1))
        mask = df["sqnr_dB"] >= sqnr_floor
        if "meets_timing" in df.columns:
            mask &= df["meets_timing"] == 1
        filt = df[mask].copy()
        mx = filt[filt.apply(lambda r: (0 in [r[c] for c in gc])
                             and (1 in [r[c] for c in gc]), axis=1)].copy()
        tgt = mx if not mx.empty else filt
        for m in MIN_OBJECTIVES:
            lo, hi = tgt[m].min(), tgt[m].max()
            tgt[f"n_{m}"] = 0.5 if lo == hi else (tgt[m] - lo) / (hi - lo)
        lo, hi = tgt[MAX_OBJECTIVE].min(), tgt[MAX_OBJECTIVE].max()
        tgt["n_sqnr"] = 0.5 if lo == hi else (tgt[MAX_OBJECTIVE] - lo) / (hi - lo)
        tgt["bs"] = np.sqrt(tgt["n_power_W"] ** 2 + tgt["n_area_LUTs"] ** 2
                            + tgt["n_crit_delay_ns"] ** 2 + (1 - tgt["n_sqnr"]) ** 2)
        best = tgt.loc[tgt["bs"].idxmin()]
        chrom = "".join(str(int(best[c])) for c in gc)
        if chrom != row["chromosome"]:
            log(f"  MISMATCH N={n}: driver {row['chromosome']} vs "
                f"optimal_designs.py {chrom}")
            ok = False
    log("selection cross-check against optimal_designs.py: "
        + ("all chromosomes agree" if ok else "DIVERGED -- do not use this table"))
    return ok


def write_selection_table(sel, diags, path, suffix, sqnr_floor):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fields = ["N", "solution_id", "chromosome", "config_type", "balance_score",
              "power_W", "energy_pJ", "area_LUTs", "crit_delay_ns", "sqnr_dB",
              "sqnr_dB_asreported", "exec_cycles", "e2e_cycles", "candidates"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv_mod.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in sel:
            w.writerow(r)

    def col(v, fmt="{}"):
        return "-" if v in (None, "") else fmt.format(v)

    COLS = [("FFT size", "points"),
            ("Solution ID", "as numbered in the sweep"),
            ("Chromosome", "one bit per stage: mult, add"),
            ("Precision mix", "mixed or fallback"),
            ("Balance score", "distance to ideal, lower better"),
            ("Power", "watts"),
            ("Area", "logic LUTs"),
            ("Critical path delay", "ns"),
            ("SQNR", "dB"),
            ("Compute cycles", "clock cycles"),
            ("Candidates considered", "solutions passing filters")]
    body = [[str(r["N"]), str(r["solution_id"]), r["chromosome"],
             "mixed" if r["config_type"] == "Mixed Precision" else "FALLBACK",
             col(r["balance_score"], "{:.4f}"), col(r["power_W"], "{:.4f}"),
             col(r["area_LUTs"], "{:.0f}"), col(r["crit_delay_ns"], "{:.3f}"),
             col(r["sqnr_dB"], "{:.2f}"), col(r["exec_cycles"]),
             str(r["candidates"])] for r in sel]
    widths = [max(len(COLS[i][0]), len(COLS[i][1]),
                  max((len(b[i]) for b in body), default=0)) for i in range(len(COLS))]

    L = ["", "=" * 78,
         "BEST BALANCED MIXED-PRECISION DESIGN PER FFT SIZE",
         "=" * 78, "",
         "Source: results/fft_<N>/all_solutions_fft<N>%s.csv" % suffix,
         "Filters: SQNR at least %.0f dB, and meets_timing = 1." % sqnr_floor,
         "Objective: minimise the Euclidean distance to the ideal corner (lowest",
         "power, smallest area, shortest critical path, highest SQNR) after min-max",
         "normalising each objective over the solutions that pass the filters:",
         "",
         "    balance score = sqrt( power^2 + area^2 + delay^2 + (1 - SQNR)^2 )",
         "                    with every term min-max normalised to [0, 1]",
         "",
         "Normalisation is per size and over the passing set, so the balance score",
         "ranks designs within one FFT size and must not be compared across sizes.",
         "",
         "  ".join(c[0].rjust(w) for c, w in zip(COLS, widths)),
         "  ".join(c[1].rjust(w) for c, w in zip(COLS, widths)),
         "-" * (sum(widths) + 2 * (len(widths) - 1))]
    for b in body:
        L.append("  ".join(v.rjust(w) for v, w in zip(b, widths)))
    fb = [r["N"] for r in sel if r["config_type"] != "Mixed Precision"]
    if fb:
        L += ["",
              "WARNING  FFT size(s) %s have no truly mixed chromosome that passes the" % fb,
              "         filters, so the winner there is uniform precision. It is not a",
              "         mixed-precision data point; say so in the paper or drop the row."]
    for d in diags:
        if d.get("note"):
            L.append("NOTE     N=%s: %s" % (d["N"], d["note"]))
    text = "\n".join(L) + "\n"
    print(text)
    log(f"selection table: {path}")
    return text


# ---------------------------------------------------------------------------
# Generation + synthesis
# ---------------------------------------------------------------------------
def design_name_for(n, sol_id):
    return f"mixed_gated_fft_{n}_sol{sol_id}"


def generate_core(row, generated_dir):
    """Regenerate with the CURRENT generator, so the core instantiates
    butterfly_wrapper_gated. Writes into its own directory; generated_cores/ is
    never touched."""
    sys.path.insert(0, REPO_ROOT)
    try:
        from fft_template_generator import FFTTemplateGenerator
    except ImportError as e:
        raise SystemExit(f"cannot import fft_template_generator from {REPO_ROOT}: {e}")

    n, sol = row["N"], row["solution_id"]
    design = design_name_for(n, sol)
    d = os.path.join(generated_dir, design)
    os.makedirs(d, exist_ok=True)
    gen = FFTTemplateGenerator(fft_size=n)
    expect = gen.get_chromosome_length()
    if len(row["chromosome"]) != expect:
        raise SystemExit(f"N={n}: chromosome {row['chromosome']} is "
                         f"{len(row['chromosome'])} bits, generator wants {expect}")
    core, top = gen.generate_verilog(row["chromosome"], os.path.join(d, f"{design}.v"))

    # The whole point of regenerating: confirm the emitted core really is gated.
    src = open(core, encoding="utf-8", errors="replace").read()
    if "butterfly_wrapper_gated" not in src:
        raise SystemExit(
            f"{core} instantiates the UNGATED butterfly_wrapper. "
            f"fft_template_generator.py is the .ungated variant -- restore the "
            f"gated instantiation before measuring, or the power numbers will not "
            f"depend on the chromosome.")
    m = re.search(r"PIPELINE_OPERANDS\s*\(\s*(\d+)\s*\)", src)
    if m and m.group(1) != "0":
        raise SystemExit(
            f"{core} sets PIPELINE_OPERANDS={m.group(1)}. That adds a register "
            f"stage, so ExecCycles from the solutions CSV no longer applies and "
            f"TOTAL_LATENCY must be bumped. This driver only supports 0.")
    return core, top, design


def parse_metrics_csv(path):
    if not os.path.exists(path):
        return None
    d = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv_mod.reader(f):
            if len(row) == 2 and row[0] != "Metric":
                d[row[0]] = row[1]
    return d


def run_vivado(cmd, log_path, timeout):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, cwd=REPO_ROOT)
    except subprocess.TimeoutExpired:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"TIMEOUT after {timeout}s\ncmd: {' '.join(cmd)}\n")
        return False, timeout, ""
    dt = time.time() - t0
    out = (r.stdout or "") + "\n===== STDERR =====\n" + (r.stderr or "")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"cmd: {' '.join(cmd)}\nreturncode: {r.returncode}\n\n")
        f.write(out)
    if r.returncode != 0:
        for ln in [l for l in (r.stdout or "").splitlines()
                   if "ERROR" in l or "CRITICAL" in l][:12]:
            log(f"    {ln.strip()}")
    return r.returncode == 0, dt, out


def synth_one(row, args, cfg):
    core, top, design = generate_core(row, args.generated_dir)
    n = row["N"]
    work = os.path.join(args.work_dir, design)
    os.makedirs(work, exist_ok=True)
    vdir = os.path.abspath(args.verilog_dir)
    saif = os.path.join(work, f"{design}.saif")
    csv_out = os.path.join(args.reports, f"{design}_metrics.csv")

    saif_ok, saif_cksum = False, None
    if not args.no_saif:
        cmd = [cfg["vivado"], "-mode", "batch", "-source", SAIF_TCL,
               "-nojournal", "-log", os.path.join(work, "saif_vivado.log"),
               "-tclargs", design, os.path.abspath(core), os.path.abspath(top),
               vdir, os.path.abspath(args.tb), os.path.abspath(saif),
               str(n), str(args.frames), args.part, str(args.clock_period)]
        saif_ok, dt, out = run_vivado(cmd, os.path.join(work, "saif_run.log"),
                                      args.timeout)
        saif_cksum = checksum_of(out)
        log(f"  {design} SAIF pass: {'ok' if saif_ok else 'FAILED'} ({dt:.0f}s)"
            f"  checksum={saif_cksum}")
        if saif_ok and not os.path.isfile(saif):
            log(f"  {design} SAIF pass returned ok but no SAIF at {saif}")
            saif_ok = False

    cmd = [cfg["vivado"], "-mode", "batch", "-source", V2_TCL,
           "-nojournal", "-log", os.path.join(work, "pwr_vivado.log"),
           "-tclargs", design, os.path.abspath(csv_out), str(args.clock_period),
           os.path.abspath(core), os.path.abspath(top), vdir, args.part,
           (os.path.abspath(saif) if saif_ok else ""),
           str(args.use_dsp), args.strip_path]
    ok, dt, out = run_vivado(cmd, os.path.join(work, "pwr_run.log"), args.timeout)
    pwr_cksum = checksum_of(out)
    cov = saif_coverage(out)
    log(f"  {design} power pass: {'ok' if ok else 'FAILED'} ({dt:.0f}s)"
        f"  checksum={pwr_cksum}")

    side = os.path.join(args.reports, f"{design}_driver.csv")
    prov = {"design": design, "chromosome": row["chromosome"],
            "solution_id": row["solution_id"],
            "synth_ok": int(ok), "saif_pass_ok": int(saif_ok),
            "saif_checksum": saif_cksum or "", "pwr_checksum": pwr_cksum or "",
            "checksum_match": int(bool(saif_cksum) and saif_cksum == pwr_cksum)}
    # Trust Vivado's net count over the TCL's power-delta heuristic, exactly as
    # objectiveEvaluationFFT.py does. This OVERRIDES saif_used from the CSV.
    if cov is not None:
        matched, total = cov
        frac = (matched / total) if total else 0.0
        prov["saif_nets_matched"] = matched
        prov["saif_nets_total"] = total
        prov["saif_coverage"] = round(frac, 4)
        prov["saif_used"] = 1 if frac >= args.min_coverage else 0
        if frac < args.min_coverage:
            log(f"  {design}: only {matched}/{total} nets ({100*frac:.0f}%) "
                f"annotated - below {100*args.min_coverage:.0f}% floor")
    elif saif_ok:
        log(f"  {design}: could not find Vivado's 'Design nets matched' line; "
            f"falling back to the TCL's saif_used heuristic, which is unreliable")
    with open(side, "w", newline="", encoding="utf-8") as f:
        w = csv_mod.writer(f)
        w.writerow(["Metric", "Value"])
        for k, v in prov.items():
            w.writerow([k, v])

    if args.clean_saif:
        reclaim_saif_workdir(design)
    return design


# ---------------------------------------------------------------------------
def num(d, k):
    try:
        return float(d[k])
    except (KeyError, TypeError, ValueError):
        return None


def derive(row, cycles, clock_ns):
    """energy_nJ  = P_dyn[W] * cycles * clock_period[ns]   (1 W x 1 ns = 1 nJ)
       thr        = f_max[Hz] / cycles                     transforms/s
       thr_per_W  = 1e9 / energy_nJ                        transforms/J
       thr_per_kLUT = thr / (LUTs/1000)"""
    if cycles is None or cycles <= 0:
        return {}
    fmax, pdyn = num(row, "fmax_mhz"), num(row, "dynamic_power_w")
    luts, used = num(row, "lut_count"), num(row, "saif_used")
    out = {}
    if fmax and fmax > 0:
        thr = fmax * 1e6 / cycles
        out["throughput_tps"] = thr
        if luts and luts > 0:
            out["throughput_per_klut"] = thr / (luts / 1000.0)
    if pdyn is not None and used == 1:
        e = pdyn * cycles * clock_ns
        out["energy_nj"] = e
        if e > 0:
            out["throughput_per_W_tpj"] = 1e9 / e
    return out


def load_cycles_file(path):
    """Optional measured override, same table shape the baselines emit."""
    out = {}
    if not path or not os.path.isfile(path):
        return out
    wide = re.compile(r"^\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(-?\d+)\s*\|\s*(-?\d+)\s*\|\s*(-?\d+)\s*\|")
    for line in open(path, encoding="utf-8", errors="replace"):
        m = wide.match(line)
        if m:
            n, ex, _l, _u, e2e = (int(g) for g in m.groups())
            out[n] = (ex, e2e if e2e > 0 else None)
    return out


def build_report(sel, args, cfg):
    override = load_cycles_file(args.cycles_file)
    rows = []
    for s in sel:
        design = design_name_for(s["N"], s["solution_id"])
        m = parse_metrics_csv(os.path.join(args.reports, f"{design}_metrics.csv"))
        if m is None:
            continue
        side = parse_metrics_csv(os.path.join(args.reports, f"{design}_driver.csv"))
        if side:
            m.update(side)
        ex, e2e = override.get(s["N"], (s["exec_cycles"], s["e2e_cycles"]))
        r = {"N": s["N"], "chromosome": s["chromosome"],
             "solution_id": s["solution_id"], "design": design,
             "exec_cycles": ex, "e2e_cycles": e2e}
        for k in ("lut_count", "lutram_count", "dsp_count", "bram_count",
                  "ff_count", "dynamic_power_w", "static_power_w",
                  "total_power_w", "dynamic_power_vectorless_w",
                  "critical_path_delay_ns", "fmax_mhz", "wns_ns",
                  "saif_used", "opt_design_ran", "use_dsp",
                  "checksum_match", "saif_checksum", "pwr_checksum",
                  "saif_coverage", "saif_nets_matched", "saif_nets_total"):
            r[k] = m.get(k, "")
        for tag, c in (("compute", ex), ("e2e", e2e)):
            for k, v in derive(m, c, args.clock_period).items():
                r[f"{tag}_{k}"] = v
        rows.append(r)

    if not rows:
        log("no per-design CSVs found; nothing to tabulate")
        return

    os.makedirs(os.path.dirname(os.path.abspath(args.csv)), exist_ok=True)
    with open(args.csv, "w", newline="", encoding="utf-8") as f:
        w = csv_mod.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    def g(r, k, fmt="{:.0f}", dash="-"):
        v = r.get(k, "")
        if v in ("", None):
            return dash
        try:
            return fmt.format(float(v))
        except (TypeError, ValueError):
            return str(v)

    def tbl(L, title, cols, rows_fn, notes=()):
        labels = [c[0] for c in cols]
        units = [c[1] for c in cols]
        body = [rows_fn(r) for r in rows]
        widths = [max(len(labels[i]), len(units[i]),
                      max((len(b[i]) for b in body), default=0))
                  for i in range(len(cols))]
        L.append("")
        L.append(title)
        L.append("  ".join(l.rjust(w) for l, w in zip(labels, widths)))
        L.append("  ".join(u.rjust(w) for u, w in zip(units, widths)))
        L.append("-" * (sum(widths) + 2 * (len(widths) - 1)))
        for b in body:
            L.append("  ".join(v.rjust(w) for v, w in zip(b, widths)))
        for nt in notes:
            L.append("  " + nt)

    def yn(v, yes="yes", no="no", unknown="?"):
        sv = str(v)
        return yes if sv == "1" else (no if sv == "0" else unknown)

    L = []
    L.append("=" * 78)
    L.append("MIXED-PRECISION FP4/FP8 CORES - FPGA SYNTHESIS, POWER AND THROUGHPUT")
    L.append("=" * 78)
    L.append("")
    L.append("What this reports: area, achievable clock frequency and dynamic power for the")
    L.append("NSGA-II-selected mixed-precision FFT cores on FPGA, regenerated with the")
    L.append("gated butterfly wrapper, plus the throughput, energy and efficiency figures")
    L.append("derived from them. Same Vivado scripts and same testbench as the FP16 and")
    L.append("FP32 baselines, so the three are comparable by construction.")
    L.append("")
    L.append("Target FPGA device              : %s" % args.part)
    L.append("Clock period used for power/XDC : %.1f ns (%.1f MHz constraint)"
             % (args.clock_period, 1000.0 / args.clock_period))
    L.append("Butterfly wrapper               : butterfly_wrapper_gated, PIPELINE_OPERANDS=0")
    L.append("                                  (combinational operand isolation, no added latency)")
    L.append("DSP inference for multipliers    : %s"
             % ("enabled" if args.use_dsp else "disabled (-max_dsp 0, multipliers in LUTs)"))
    L.append("Synthesis script                : vivado_synthesis_v2.tcl (unmodified)")
    L.append("Switching-activity script       : generate_saif_funcsim.tcl (unmodified)")
    L.append("Activity testbench              : %s, strip path %s"
             % (os.path.basename(args.tb), args.strip_path))
    L.append("Minimum accepted SAIF coverage  : %.0f%% of design nets" % (100 * args.min_coverage))
    L.append("")
    L.append("The clock constraint above only sets the operating point at which dynamic")
    L.append("power is reported. Energy per transform is frequency-invariant, so it is the")
    L.append("same physical quantity whatever constraint is used.")

    tbl(L, "TABLE 1  DESIGN UNDER TEST", [
        ("FFT size", "points"),
        ("Solution ID", "as numbered in the sweep"),
        ("Chromosome", "one bit per stage: mult, add"),
        ("FP8 multiply stages", "of total stages"),
        ("FP8 add stages", "of total stages"),
    ], lambda r: [
        str(r["N"]), str(r["solution_id"]), r["chromosome"],
        "%d of %d" % (sum(int(b) for b in r["chromosome"][0::2]), len(r["chromosome"]) // 2),
        "%d of %d" % (sum(int(b) for b in r["chromosome"][1::2]), len(r["chromosome"]) // 2),
    ], notes=[
        "A 1 selects the FP8 datapath for that stage, a 0 selects FP4. Bits run",
        "stage 0 first, multiply bit before add bit.",
    ])

    tbl(L, "TABLE 2  RESOURCE UTILISATION", [
        ("FFT size", "points"),
        ("Logic LUTs", "count"),
        ("LUTs used as memory", "count"),
        ("Flip-flops", "count"),
        ("Block RAM tiles", "count"),
        ("DSP slices", "count"),
    ], lambda r: [str(r["N"]), g(r, "lut_count"), g(r, "lutram_count"),
                  g(r, "ff_count"), g(r, "bram_count"), g(r, "dsp_count")])

    tbl(L, "TABLE 3  TIMING AND POWER", [
        ("FFT size", "points"),
        ("Maximum frequency", "MHz"),
        ("Critical path delay", "ns"),
        ("Worst negative slack", "ns"),
        ("Dynamic power", "watts"),
        ("Static power", "watts"),
        ("Total on-chip power", "watts"),
    ], lambda r: [str(r["N"]), g(r, "fmax_mhz", "{:.2f}"),
                  g(r, "critical_path_delay_ns", "{:.3f}"), g(r, "wns_ns", "{:.3f}"),
                  g(r, "dynamic_power_w", "{:.4f}"), g(r, "static_power_w", "{:.4f}"),
                  g(r, "total_power_w", "{:.4f}")], notes=[
        "Maximum frequency is 1 / critical path delay, not the constrained frequency.",
    ])

    tbl(L, "TABLE 4  ARE THE POWER NUMBERS TRUSTWORTHY?", [
        ("FFT size", "points"),
        ("Switching activity annotated", "yes / no"),
        ("Design nets annotated", "percent of total"),
        ("Netlist checksums agree", "yes / no"),
        ("Post-synthesis optimisation ran", "yes / no"),
        ("Vectorless dynamic power", "watts, for reference"),
    ], lambda r: [
        str(r["N"]), yn(r.get("saif_used")),
        ("{:.0f}".format(100 * float(r["saif_coverage"]))
         if r.get("saif_coverage") not in ("", None) else "-"),
        yn(r.get("checksum_match")), yn(r.get("opt_design_ran")),
        g(r, "dynamic_power_vectorless_w", "{:.4f}"),
    ], notes=[
        "This matters more here than for the baselines: the gated wrapper is the only",
        "reason dynamic power depends on the chromosome at all, and that dependence is",
        "invisible without a real switching-activity annotation. A row with 'no' here",
        "carries no information about the precision schedule.",
    ])

    for tag, title, note_lines in (
            ("compute",
             "TABLE 5  THROUGHPUT AND EFFICIENCY, COMPUTE ONLY (start of transform to done)",
             ["Isolates the datapath, and is the column to compare against the FP16 and",
              "FP32 baselines, because load and unload cost the same at every precision."]),
            ("e2e",
             "TABLE 6  THROUGHPUT AND EFFICIENCY, END TO END (load + compute + unload)",
             ["Compute cycles plus 4N + 1: load is N+1 cycles and unload is 3N, from the",
              "load/unload loops in performance_evaluator.py's testbench. Pass",
              "--cycles-file to substitute a measured table."]),
    ):
        ckey = "exec_cycles" if tag == "compute" else "e2e_cycles"
        tbl(L, title, [
            ("FFT size", "points"),
            ("Cycles per transform", "clock cycles"),
            ("Throughput", "transforms per second"),
            ("Energy per transform", "nanojoules"),
            ("Throughput per watt", "transforms per joule"),
            ("Throughput per 1000 logic LUTs", "transforms per second"),
        ], lambda r, t=tag, c=ckey: [
            str(r["N"]), g(r, c),
            g(r, "%s_throughput_tps" % t, "{:,.0f}"),
            g(r, "%s_energy_nj" % t, "{:.2f}"),
            g(r, "%s_throughput_per_W_tpj" % t, "{:,.0f}"),
            g(r, "%s_throughput_per_klut" % t, "{:,.0f}"),
        ], notes=note_lines)

    L.append("")
    L.append("HOW THE DERIVED COLUMNS ARE COMPUTED")
    L.append("  Throughput (transforms/s)      = maximum frequency / cycles per transform")
    L.append("  Energy per transform (nJ)      = dynamic power x cycles x clock period")
    L.append("  Throughput per watt (transf/J) = 1 / energy per transform. These are the")
    L.append("                                   same quantity, not a separate measurement.")
    L.append("  Throughput per 1000 logic LUTs = throughput / (logic LUTs / 1000)")

    bad = [r["N"] for r in rows if str(r.get("saif_used")) != "1"]
    if bad:
        L.append("")
        L.append("WARNING  Switching activity was not annotated for FFT size(s) %s." % bad)
        L.append("         Energy per transform and throughput per watt are omitted there.")
    mism = [r["N"] for r in rows if str(r.get("checksum_match")) == "0"]
    if mism:
        L.append("")
        L.append("WARNING  Netlist checksums disagree for FFT size(s) %s. The activity" % mism)
        L.append("         file describes a different netlist than the one annotated.")

    text = "\n".join(L) + "\n"
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print("\n" + text)
    log(f"table: {args.out}")
    log(f"csv  : {args.csv}")


def main():
    cfg = read_globals()
    ap = argparse.ArgumentParser(
        description="Mixed-precision FFT FPGA PPA + throughput, gated wrapper "
                    "(Vivado v2 + funcsim SAIF)")
    ap.add_argument("--sizes", type=int, nargs="*", default=ALL_SIZES)
    ap.add_argument("--clock-period", type=float, default=cfg["clock"],
                    help=f"power/XDC clock in ns (default {cfg['clock']} from "
                         f"POWER_CLOCK_NS)")
    ap.add_argument("--part", default=cfg["part"])
    ap.add_argument("--vivado", default=cfg["vivado"])
    ap.add_argument("--strip-path", default=cfg["strip"],
                    help=f"read_saif -strip_path (default {cfg['strip']} from "
                         f"SAIF_STRIP_PATH)")
    ap.add_argument("--min-coverage", type=float, default=cfg["min_cov"])
    ap.add_argument("--use-dsp", type=int, choices=(0, 1), default=cfg["use_dsp"])
    ap.add_argument("--frames", type=int, default=cfg["frames"])
    ap.add_argument("--timeout", type=int, default=cfg["timeout"])
    ap.add_argument("--clean-saif", dest="clean_saif", action="store_true",
                    default=cfg["clean_saif"])
    ap.add_argument("--keep-saif-workdir", dest="clean_saif", action="store_false")
    ap.add_argument("--no-saif", action="store_true",
                    help="skip the SAIF pass. Area and timing stay valid; energy "
                         "and throughput/W are omitted, not guessed.")
    ap.add_argument("--results-dir", default=DEFAULT_RESULTS)
    ap.add_argument("--solutions-suffix", default="_fixed",
                    help="'_fixed' (default) reads all_solutions_fft<N>_fixed.csv, "
                         "with fix_sqnr.py's repaired SQNR and energy_pJ. Pass '' "
                         "to select from the original CSV, whose sqnr_dB is the "
                         "mirrored-parabola artefact.")
    ap.add_argument("--sqnr-floor", type=float, default=15.0,
                    help="minimum SQNR in dB (default 15, as optimal_designs.py)")
    ap.add_argument("--chromosome",
                    help="override the selection for a single size (use with one "
                         "--sizes value)")
    ap.add_argument("--solution-id", type=int, default=0,
                    help="solution id to label a --chromosome override")
    ap.add_argument("--verify-selection", action="store_true",
                    help="re-derive the selection with pandas exactly as "
                         "optimal_designs.py does and assert the chromosomes agree")
    ap.add_argument("--verilog-dir", default=DEFAULT_VERILOG)
    ap.add_argument("--generated-dir", default=DEFAULT_GENERATED)
    ap.add_argument("--tb", default=DEFAULT_TB)
    ap.add_argument("--work-dir", default=DEFAULT_WORK)
    ap.add_argument("--reports", default=DEFAULT_WORK)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--csv", default=DEFAULT_CSV)
    ap.add_argument("--selection-csv", default=DEFAULT_SEL)
    ap.add_argument("--cycles-file", default=None,
                    help="optional measured cycle table to override the CSV's "
                         "avg_exec_cycles and the derived end-to-end count")
    ap.add_argument("--select-only", action="store_true",
                    help="print and store the selection table, then stop")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    cfg["vivado"] = args.vivado

    for n in args.sizes:
        if n & (n - 1) or n < 2 or n > 1024:
            raise SystemExit(f"{n} is not a power of two in [2, 1024]")

    if args.chromosome:
        if len(args.sizes) != 1:
            raise SystemExit("--chromosome applies to one size; pass a single --sizes")
        n = args.sizes[0]
        sel = [{"N": n, "solution_id": args.solution_id,
                "chromosome": args.chromosome.strip(),
                "config_type": "Manual override", "balance_score": None,
                "power_W": None, "area_LUTs": None, "crit_delay_ns": None,
                "sqnr_dB": None, "sqnr_dB_asreported": None, "energy_pJ": None,
                "exec_cycles": None, "e2e_cycles": None, "candidates": 0}]
        diags = [{"N": n, "note": "chromosome supplied on the command line; "
                                  "ExecCycles unknown, so throughput needs "
                                  "--cycles-file"}]
    else:
        sel, diags = [], []
        for n in args.sizes:
            row, d = select_best(n, args.results_dir, args.solutions_suffix,
                                 args.sqnr_floor)
            diags.append(d)
            if row is None:
                log(f"N={n}: no design selected ({d['note']})")
                continue
            sel.append(row)
        if not sel:
            raise SystemExit("no design selected for any requested size")

    write_selection_table(sel, diags, args.selection_csv,
                          args.solutions_suffix, args.sqnr_floor)
    if args.verify_selection and not args.chromosome:
        if not verify_selection_with_pandas(sel, args.results_dir,
                                            args.solutions_suffix, args.sqnr_floor):
            raise SystemExit("selection diverged from optimal_designs.py; stopping")
    if args.select_only:
        return

    os.makedirs(args.reports, exist_ok=True)
    if not args.report_only:
        for p, what in ((V2_TCL, "vivado_synthesis_v2.tcl"),
                        (SAIF_TCL, "generate_saif_funcsim.tcl"),
                        (args.tb, "activity testbench")):
            if not os.path.isfile(p):
                raise SystemExit(f"{what} not found at {p}")
        if not os.path.isdir(args.verilog_dir):
            raise SystemExit(f"verilog_sources not found at {args.verilog_dir}")
        if not os.path.exists(args.vivado):
            raise SystemExit(f"vivado not found at {args.vivado}\n"
                             f"Pass --vivado /path/to/vivado")
        log(f"part {args.part}  clock {args.clock_period} ns  use_dsp={args.use_dsp}")
        log(f"sizes {[r['N'] for r in sel]}")
        for row in sel:
            log(f"=== N={row['N']} sol{row['solution_id']} "
                f"chromosome {row['chromosome']} ===")
            synth_one(row, args, cfg)

    build_report(sel, args, cfg)


if __name__ == "__main__":
    main()
