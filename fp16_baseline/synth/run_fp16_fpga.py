#!/usr/bin/env python3
"""
FP16 Baseline FPGA PPA + Throughput  --  Vivado, v2 flow, funcsim SAIF
=====================================================================
This is the FPGA track. It does NOT fork either of the repo's Vivado scripts:
`vivado_synthesis_v2.tcl` and `generate_saif_funcsim.tcl` are already
format-agnostic (verilog_dir, tb_file, use_dsp and saif_strip_path are all
arguments), so the FP16 baseline goes through the exact same, already-validated
scripts the mixed-precision sweep uses. That is the whole point: one flow, so
the numbers are comparable by construction.

What this driver adds:

  1. **Source assembly.** The TCL scripts glob ONE directory. FP16's RTL spans
     source/, source/mem_regarray/ and ../verilog_sources/ (agu, bit_reversal),
     so the driver stages exactly the needed .v files into a flat scratch dir
     and passes that as verilog_dir. Nothing is copied into the repo.

  2. **The two-pass SAIF discipline**, per claude/saif-power-flow-working.md:
       pass 1  generate_saif_funcsim.tcl  -- synthesise, write a funcsim
               netlist, simulate THAT netlist against tb/tb_fp16_power.v under
               xsim, log a SAIF whose net names match the netlist by
               construction.
       pass 2  vivado_synthesis_v2.tcl    -- synthesise identically, annotate
               that SAIF, report utilisation / timing / split power.
     Both passes print `Synth Design complete | Checksum:`; the driver compares
     them and records `checksum_match`. Unequal means the SAIF describes a
     different netlist than the one annotated, and the design is marked
     untrustworthy rather than silently used.

  3. **saif_used gating.** v2 already measures vectorless power first and then
     re-measures with the SAIF, setting saif_used=0 if dynamic power did not
     move. The driver refuses to report energy for a design with saif_used=0,
     because vectorless power on an XC7A35T is ~65 % static and takes three
     values across the whole design space.

  4. **The derived metrics**, both cycle denominators side by side:

       Throughput (transforms/s) = f_max / cycles
       Energy/FFT (nJ)           = P_dynamic(W) x cycles x clock_period(ns)
       Throughput/W (transf/J)   = 1e9 / Energy/FFT(nJ)
       Throughput/kLUT           = Throughput / (LUTs / 1000)

     with cycles = ExecCycles (compute-only, start-to-done) AND
          cycles = E2E cycles  (load + exec + unload, non-overlapped)
     both read from sim/perf/fp16_sqnr_results.txt.

     Throughput/W is *identically* 1/energy-per-transform -- dimensionally
     (transforms/s)/(J/s) = transforms/J -- so it is derived, not separately
     measured, and it is frequency-invariant for the same reason energy is
     (Vivado's dynamic power scales linearly with the clock constraint).

Memory: the FPGA track uses the register-array memory
(source/mem_regarray/fp16_memory.v), which Vivado infers as BRAM. The
SRAM-macro variant is deliberately NOT offered here -- an OpenRAM macro is a
blackbox Vivado cannot map, so an FPGA run against it is meaningless.

Usage (from the repository root):
    python3 fp16_baseline/synth/run_fp16_fpga.py --sizes 256
    python3 fp16_baseline/synth/run_fp16_fpga.py                    # all 10
    python3 fp16_baseline/synth/run_fp16_fpga.py --use-dsp 0        # LUT-only area
    python3 fp16_baseline/synth/run_fp16_fpga.py --report-only      # re-tabulate

Run sim/fp16_performance_evaluator.py first (run_fp16_design.py does this in
order) so the cycle counts exist; without them throughput and energy are
reported as N/A rather than guessed.

Output: synth/fp16_fpga_report.txt + synth/fp16_fpga_metrics.csv, with per-design
Vivado CSVs and logs under synth/fpga_work/.
"""

import argparse
import csv as csv_mod
import os
import re
import shutil
import subprocess
import sys
import time

SYNTH_DIR = os.path.dirname(os.path.abspath(__file__))       # fp16_baseline/synth
BASE_DIR = os.path.dirname(SYNTH_DIR)                         # fp16_baseline
REPO_ROOT = os.path.dirname(BASE_DIR)                         # repo root

ALL_SIZES = [2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]

# The repo's own scripts -- used unmodified.
V2_TCL = os.path.join(REPO_ROOT, "vivado_synthesis_v2.tcl")
SAIF_TCL = os.path.join(REPO_ROOT, "generate_saif_funcsim.tcl")
IMPL_TCL = os.path.join(REPO_ROOT, "vivado_implement.tcl")

DEFAULT_SOURCE_DIR = os.path.join(BASE_DIR, "source")
DEFAULT_SHARED_DIR = os.path.join(REPO_ROOT, "verilog_sources")
DEFAULT_GENERATED_DIR = os.path.join(BASE_DIR, "generated_cores")
DEFAULT_TB = os.path.join(BASE_DIR, "tb", "tb_fp16_power.v")
DEFAULT_WORK = os.path.join(SYNTH_DIR, "fpga_work")
DEFAULT_OUT = os.path.join(SYNTH_DIR, "fp16_fpga_report.txt")
DEFAULT_CSV = os.path.join(SYNTH_DIR, "fp16_fpga_metrics.csv")
DEFAULT_CYCLES = os.path.join(BASE_DIR, "sim", "perf", "fp16_sqnr_results.txt")

# Format RTL that is common to both memory tracks.
FORMAT_RTL = ["fp16_adder.v", "fp16_butterfly.v", "fp16_multiplier.v",
              "fp16_twiddle_rom.v"]
# FPGA track memory.
MEM_RTL = os.path.join("mem_regarray", "fp16_memory.v")
SHARED_RTL = ["agu.v", "bit_reversal.v"]

CHECKSUM_RE = re.compile(r"Synth Design complete\s*\|\s*Checksum:\s*(\S+)")
# Vivado's own annotation count. objectiveEvaluationFFT.py gates saif_used on
# this rather than on the TCL's power-delta heuristic, because at three decimal
# places an annotated and a vectorless run frequently round to the same value
# even when annotation worked (see claude/saif-power-flow-working.md:
# fft_16_sol12_gen1, 844/1908 nets annotated, both powers 0.034 W, flag inert).
SAIF_NETS_RE = re.compile(r"Design nets matched\s*=\s*(\d+)\s+of\s+(\d+)")


def saif_coverage(text):
    """(matched, total) from a power-pass log, or None."""
    m = SAIF_NETS_RE.search(text or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def reclaim_saif_workdir(design):
    """generate_saif_funcsim.tcl leaves /tmp/fsaif_<design>/ holding a funcsim
    netlist and an xsim snapshot -- tens of MB each, enough to fill /tmp over a
    ten-size sweep. Both passes have read it by the time this runs. Best effort:
    a sweep must never die because scratch could not be removed."""
    work = os.path.join("/tmp", f"fsaif_{design}")
    if not os.path.isdir(work):
        return
    try:
        shutil.rmtree(work)
    except OSError as e:
        log(f"  could not reclaim {work}: {e}")


def log(msg):
    print(f"[fp16-fpga] {msg}", flush=True)


def read_globals():
    """Scrape VIVADO_PATH / POWER_CLOCK_NS / FPGA_DEVICE from the project's own
    globals rather than duplicating them. Text-scrape, not import: importing
    globalVariablesMixedFFT creates directories and appends to optimization.log."""
    d = {"vivado": "/home/digital-1/2025.2/Vivado/bin/vivado",
         "clock": 10.0, "part": "xc7a35tcpg236-1",
         "frames": 4, "use_dsp": 1, "min_cov": 0.10,
         "timeout": 1800, "clean_saif": True}
    p = os.path.join(REPO_ROOT, "globalVariablesMixedFFT.py")
    if not os.path.exists(p):
        return d
    t = open(p, encoding="utf-8", errors="replace").read()
    m = re.search(r"^VIVADO_PATH\s*=\s*['\"](.+?)['\"]", t, re.M)
    if m:
        d["vivado"] = m.group(1)
    # POWER_CLOCK_NS is the power/XDC clock; CLOCK_PERIOD is only the latency
    # normaliser (see claude/saif-power-flow-working.md, "resolution trap").
    m = re.search(r"^POWER_CLOCK_NS\s*=\s*([0-9.]+)", t, re.M)
    if m:
        d["clock"] = float(m.group(1))
    m = re.search(r"^FPGA_DEVICE\s*=\s*['\"](.+?)['\"]", t, re.M)
    if m:
        d["part"] = m.group(1)
    # The rest of the mixed sweep's parity constants, so this driver cannot
    # silently drift from objectiveEvaluationFFT.py._run_vivado_synthesis.
    for key, pat, cast in (
            ("frames",  r"^SAIF_FRAMES\s*=\s*(\d+)", int),
            ("use_dsp", r"^USE_DSP\s*=\s*(\d+)", int),
            ("min_cov", r"^SAIF_MIN_COVERAGE\s*=\s*([0-9.]+)", float),
            ("timeout", r"^VIVADO_TIMEOUT_S\s*=\s*(\d+)", int)):
        mm = re.search(pat, t, re.M)
        if mm:
            d[key] = cast(mm.group(1))
    mm = re.search(r"^CLEAN_SAIF_WORKDIRS\s*=\s*(True|False)", t, re.M)
    if mm:
        d["clean_saif"] = (mm.group(1) == "True")
    return d


def stage_sources(n, source_dir, shared_dir, dest):
    """Flatten the FP16 source set into one directory, because both TCL scripts
    glob a single verilog_dir. Only the FPGA-track memory is included."""
    os.makedirs(dest, exist_ok=True)
    for f in os.listdir(dest):
        if f.endswith(".v"):
            os.remove(os.path.join(dest, f))

    wanted = [os.path.join(source_dir, f) for f in FORMAT_RTL]
    wanted.append(os.path.join(source_dir, MEM_RTL))
    wanted += [os.path.join(shared_dir, f) for f in SHARED_RTL]

    missing = [p for p in wanted if not os.path.isfile(p)]
    if missing:
        raise SystemExit("missing source files:\n  " + "\n  ".join(missing))

    for p in wanted:
        shutil.copy2(p, os.path.join(dest, os.path.basename(p)))
    return dest


def core_and_top(n, generated_dir):
    d = os.path.join(generated_dir, f"fp16_fft_{n}")
    core = os.path.join(d, f"fp16_fft_{n}_core.v")
    top = os.path.join(d, f"fp16_fft_{n}_top.v")
    for p in (core, top):
        if not os.path.isfile(p):
            raise SystemExit(f"{p} missing -- run fp16_template_generator.py first")
    return core, top


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


def checksum_of(text):
    m = CHECKSUM_RE.search(text or "")
    return m.group(1) if m else None


def parse_metrics_csv(path):
    if not os.path.exists(path):
        return None
    d = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv_mod.reader(f):
            if len(row) == 2 and row[0] != "Metric":
                d[row[0]] = row[1]
    return d


def load_cycles(path):
    """Parse the evaluator table into {N: (exec, e2e)}.

    Handles both the current table (Exec | Load | Unload | E2E | ...) and the
    older one (Exec | ...), in which case e2e is None."""
    out = {}
    if not os.path.isfile(path):
        return out
    wide = re.compile(r"^\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(-?\d+)\s*\|\s*(-?\d+)\s*\|\s*(-?\d+)\s*\|")
    narrow = re.compile(r"^\s*(\d+)\s*\|\s*(\d+)\s*\|")
    for line in open(path, encoding="utf-8", errors="replace"):
        m = wide.match(line)
        if m:
            n, ex, _ld, _ul, e2e = (int(g) for g in m.groups())
            out[n] = (ex, e2e if e2e > 0 else None)
            continue
        m = narrow.match(line)
        if m:
            n, ex = int(m.group(1)), int(m.group(2))
            out.setdefault(n, (ex, None))
    return out


def fnum(d, k):
    try:
        return float(d[k])
    except (KeyError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
def synth_one(n, args, cfg):
    design = f"fp16_fft_{n}"
    work = os.path.join(args.work_dir, design)
    os.makedirs(work, exist_ok=True)

    core, top = core_and_top(n, args.generated_dir)
    vdir = stage_sources(n, args.source_dir, args.shared_dir,
                         os.path.join(work, "srcs"))
    saif = os.path.join(work, f"{design}.saif")
    csv_out = os.path.join(args.reports, f"{design}_metrics.csv")
    strip_path = "tb_fp16_power/uut"

    saif_ok = False
    saif_cksum = None
    if not args.no_saif:
        cmd = [cfg["vivado"], "-mode", "batch", "-source", SAIF_TCL,
               "-nojournal", "-log", os.path.join(work, "saif_vivado.log"),
               "-tclargs", design, os.path.abspath(core), os.path.abspath(top),
               os.path.abspath(vdir), os.path.abspath(args.tb),
               os.path.abspath(saif), str(n), str(args.frames),
               args.part, str(args.clock_period)]
        saif_ok, dt, out = run_vivado(cmd, os.path.join(work, "saif_run.log"),
                                      args.timeout)
        saif_cksum = checksum_of(out)
        log(f"  {design} SAIF pass: {'ok' if saif_ok else 'FAILED'} ({dt:.0f}s)"
            f"  checksum={saif_cksum}")
        if saif_ok and not os.path.isfile(saif):
            log(f"  {design} SAIF pass returned ok but no SAIF at {saif}")
            saif_ok = False

    cmd = [cfg["vivado"], "-mode", "batch", "-source", (IMPL_TCL if args.implement else V2_TCL),
           "-nojournal", "-log", os.path.join(work, "pwr_vivado.log"),
           "-tclargs", design, os.path.abspath(csv_out), str(args.clock_period),
           os.path.abspath(core), os.path.abspath(top), os.path.abspath(vdir),
           args.part,
           (os.path.abspath(saif) if saif_ok else ""),
           str(args.use_dsp), strip_path]
    if args.implement:
        cmd.append(os.path.abspath(
            os.path.splitext(csv_out)[0] + "_impl.csv"))
    ok, dt, out = run_vivado(cmd, os.path.join(work, "pwr_run.log"), args.timeout)
    pwr_cksum = checksum_of(out)
    cov = saif_coverage(out)
    log(f"  {design} power pass: {'ok' if ok else 'FAILED'} ({dt:.0f}s)"
        f"  checksum={pwr_cksum}")

    # Vivado's CSV cannot carry the driver's own provenance (the two checksums
    # come from two separate invocations), so write a sidecar next to it. This
    # is what makes --report-only able to show checksum_match.
    side = os.path.join(args.reports, f"{design}_driver.csv")
    prov = {
        "synth_ok": int(ok),
        "saif_pass_ok": int(saif_ok),
        "saif_checksum": saif_cksum or "",
        "pwr_checksum": pwr_cksum or "",
        "checksum_match": int(bool(saif_cksum) and saif_cksum == pwr_cksum),
    }
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

    m = parse_metrics_csv(csv_out) or {}
    m.update({k: str(v) for k, v in prov.items()})

    if args.clean_saif:
        reclaim_saif_workdir(design)
    return m


# ---------------------------------------------------------------------------
def derive(row, cycles, clock_ns):
    """Throughput / energy / efficiency for one cycle denominator.

    energy_nJ  = P_dyn[W] * cycles * clock_period[ns]        (1 W x 1 ns = 1 nJ)
    thr        = f_max[Hz] / cycles                          transforms/s
    thr_per_W  = 1e9 / energy_nJ                             transforms/J
                 (identically (transforms/s)/(J/s); frequency-invariant, since
                  Vivado's dynamic power scales linearly with the constraint)
    thr_per_kLUT = thr / (LUTs/1000)
    """
    if cycles is None or cycles <= 0:
        return {}
    fmax = fnum(row, "fmax_mhz")
    pdyn = fnum(row, "dynamic_power_w")
    luts = fnum(row, "lut_count")
    saif_used = fnum(row, "saif_used")

    out = {}
    if fmax and fmax > 0:
        thr = fmax * 1e6 / cycles
        out["throughput_tps"] = thr
        if luts and luts > 0:
            out["throughput_per_klut"] = thr / (luts / 1000.0)
    # Energy only where the SAIF actually annotated -- vectorless power on this
    # part is ~65 % static and takes three values across the design space.
    if pdyn is not None and saif_used == 1:
        e_nj = pdyn * cycles * clock_ns
        out["energy_nj"] = e_nj
        if e_nj > 0:
            out["throughput_per_W_tpj"] = 1e9 / e_nj
    return out


def build_report(sizes, args, cfg):
    cyc = load_cycles(args.cycles_file)
    rows = []
    for n in sizes:
        m = parse_metrics_csv(os.path.join(args.reports, f"fp16_fft_{n}_metrics.csv"))
        if m is None:
            continue
        side = parse_metrics_csv(os.path.join(args.reports, f"fp16_fft_{n}_driver.csv"))
        if side:
            m.update(side)
        ex, e2e = cyc.get(n, (None, None))
        r = {"N": n, "exec_cycles": ex, "e2e_cycles": e2e}
        for k in ("lut_count", "lutram_count", "dsp_count", "bram_count",
                  "ff_count", "dynamic_power_w", "static_power_w",
                  "total_power_w", "dynamic_power_vectorless_w",
                  "critical_path_delay_ns", "fmax_mhz", "wns_ns",
                  "saif_used", "saif_read", "opt_design_ran", "use_dsp",
                  "dsp48_primitives", "checksum_match", "saif_checksum",
                  "pwr_checksum", "saif_coverage", "saif_nets_matched",
                  "saif_nets_total"):
            r[k] = m.get(k, "")
        for tag, c in (("compute", ex), ("e2e", e2e)):
            for k, v in derive(m, c, args.clock_period).items():
                r[f"{tag}_{k}"] = v
        rows.append(r)

    if not rows:
        log("no per-design CSVs found; nothing to tabulate")
        return

    os.makedirs(os.path.dirname(os.path.abspath(args.csv)), exist_ok=True)
    fields = list(rows[0].keys())
    with open(args.csv, "w", newline="", encoding="utf-8") as f:
        w = csv_mod.DictWriter(f, fieldnames=fields)
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
        """Render a table with a two-line header: full-word label, then unit.
        Column widths follow whichever of label / unit / values is widest."""
        labels = [c[0] for c in cols]
        units = [c[1] for c in cols]
        body = [rows_fn(r) for r in rows]
        widths = []
        for i in range(len(cols)):
            w = max(len(labels[i]), len(units[i]),
                    max((len(b[i]) for b in body), default=0))
            widths.append(w)
        L.append("")
        L.append(title)
        L.append("  ".join(l.rjust(w) for l, w in zip(labels, widths)))
        L.append("  ".join(u.rjust(w) for u, w in zip(units, widths)))
        L.append("-" * (sum(widths) + 2 * (len(widths) - 1)))
        for b in body:
            L.append("  ".join(v.rjust(w) for v, w in zip(b, widths)))
        for n in notes:
            L.append("  " + n)

    def yn(v, yes="yes", no="no", unknown="?"):
        sv = str(v)
        if sv == "1":
            return yes
        if sv == "0":
            return no
        return unknown

    L = []
    L.append("=" * 78)
    L.append("FP16 BASELINE - FPGA SYNTHESIS, POWER AND THROUGHPUT")
    L.append("=" * 78)
    L.append("")
    L.append("What this reports: area, achievable clock frequency and dynamic power for the")
    L.append("FP16 reference FFT cores on FPGA, plus the throughput, energy and efficiency")
    L.append("figures derived from them.")
    L.append("")
    L.append("Target FPGA device              : %s" % args.part)
    L.append("Clock period used for power/XDC : %.1f ns (%.1f MHz constraint)"
             % (args.clock_period, 1000.0 / args.clock_period))
    L.append("Memory implementation           : register arrays (Vivado infers Block RAM)")
    L.append("DSP inference for multipliers   : %s"
             % ("enabled" if args.use_dsp else "disabled (-max_dsp 0, multipliers in LUTs)"))
    L.append("Synthesis script                : vivado_synthesis_v2.tcl (unmodified)")
    L.append("Switching-activity script       : generate_saif_funcsim.tcl (unmodified)")
    L.append("Minimum accepted SAIF coverage  : %.0f%% of design nets" % (100 * args.min_coverage))
    L.append("")
    L.append("The clock constraint above only sets the operating point at which dynamic")
    L.append("power is reported. Energy per transform is frequency-invariant, so it is the")
    L.append("same physical quantity whatever constraint is used.")

    tbl(L, "TABLE 1  RESOURCE UTILISATION", [
        ("FFT size", "points"),
        ("Logic LUTs", "count"),
        ("LUTs used as memory", "count"),
        ("Flip-flops", "count"),
        ("Block RAM tiles", "count"),
        ("DSP slices", "count"),
    ], lambda r: [
        str(r["N"]),
        g(r, "lut_count"), g(r, "lutram_count"), g(r, "ff_count"),
        g(r, "bram_count"), g(r, "dsp_count"),
    ])

    tbl(L, "TABLE 2  TIMING AND POWER", [
        ("FFT size", "points"),
        ("Maximum frequency", "MHz"),
        ("Critical path delay", "ns"),
        ("Worst negative slack", "ns"),
        ("Dynamic power", "watts"),
        ("Static power", "watts"),
        ("Total on-chip power", "watts"),
    ], lambda r: [
        str(r["N"]),
        g(r, "fmax_mhz", "{:.2f}"), g(r, "critical_path_delay_ns", "{:.3f}"),
        g(r, "wns_ns", "{:.3f}"),
        g(r, "dynamic_power_w", "{:.4f}"), g(r, "static_power_w", "{:.4f}"),
        g(r, "total_power_w", "{:.4f}"),
    ], notes=[
        "Maximum frequency is 1 / critical path delay, not the constrained frequency.",
    ])

    tbl(L, "TABLE 3  ARE THE POWER NUMBERS TRUSTWORTHY?", [
        ("FFT size", "points"),
        ("Switching activity annotated", "yes / no"),
        ("Design nets annotated", "percent of total"),
        ("Netlist checksums agree", "yes / no"),
        ("Post-synthesis optimisation ran", "yes / no"),
        ("Vectorless dynamic power", "watts, for reference"),
    ], lambda r: [
        str(r["N"]),
        yn(r.get("saif_used")),
        ("{:.0f}".format(100 * float(r["saif_coverage"]))
         if r.get("saif_coverage") not in ("", None) else "-"),
        yn(r.get("checksum_match")),
        yn(r.get("opt_design_ran")),
        g(r, "dynamic_power_vectorless_w", "{:.4f}"),
    ], notes=[
        "'Switching activity annotated' is Vivado's own count of design nets matched by",
        "the activity file, tested against the coverage floor above. Where it reads 'no',",
        "energy and throughput-per-watt are left blank in the tables below rather than",
        "reported from vectorless power, which on this device is mostly static leakage",
        "and barely varies between designs. The dynamic power in Table 2 and the",
        "vectorless figure here must differ; if they are equal, the activity file was",
        "not really applied.",
    ])

    for tag, title, note_lines in (
            ("compute",
             "TABLE 4  THROUGHPUT AND EFFICIENCY, COMPUTE ONLY (start of transform to done)",
             ["Isolates the datapath. This is the cycle count the rest of the flow treats",
              "as canonical, and the fair basis for comparing one precision against",
              "another, because the load and unload overhead is identical across",
              "precisions."]),
            ("e2e",
             "TABLE 5  THROUGHPUT AND EFFICIENCY, END TO END (load + compute + unload)",
             ["What the non-overlapped system actually achieves per transform. The two",
              "memory banks ping-pong between FFT stages, so they cannot also",
              "double-buffer input against compute; overlapping the two would need a",
              "third bank."]),
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
            str(r["N"]),
            g(r, c),
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
    L.append("                                   same quantity: transforms per second")
    L.append("                                   divided by joules per second is")
    L.append("                                   transforms per joule. It is not a")
    L.append("                                   separate measurement.")
    L.append("  Throughput per 1000 logic LUTs = throughput / (logic LUTs / 1000)")

    bad = [r["N"] for r in rows if str(r.get("saif_used")) != "1"]
    if bad:
        L.append("")
        L.append("WARNING  Switching activity was not annotated for FFT size(s) %s." % bad)
        L.append("         Energy per transform and throughput per watt are omitted for")
        L.append("         those rows. Check the SAIF strip path and the activity-pass log.")
    mism = [r["N"] for r in rows if str(r.get("checksum_match")) == "0"]
    if mism:
        L.append("")
        L.append("WARNING  Netlist checksums disagree for FFT size(s) %s. The activity" % mism)
        L.append("         file describes a different netlist than the one it was applied")
        L.append("         to. Do not trust those power numbers.")
    if any(r.get("e2e_cycles") in (None, "") for r in rows):
        L.append("")
        L.append("NOTE     End-to-end cycles are missing for some sizes: the cycle-count")
        L.append("         table predates the load and unload columns. Re-run")
        L.append("         sim/fp16_performance_evaluator.py to populate them.")

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
        description="FP16 baseline FPGA PPA + throughput (Vivado v2 + funcsim SAIF)")
    ap.add_argument("--sizes", type=int, nargs="*", default=ALL_SIZES)
    ap.add_argument("--clock-period", type=float, default=cfg["clock"],
                    help=f"power/XDC clock in ns (default {cfg['clock']} from "
                         f"POWER_CLOCK_NS). Energy is frequency-invariant, so this "
                         f"buys significant figures on dynamic power, not a different "
                         f"physical quantity.")
    ap.add_argument("--part", default=cfg["part"])
    ap.add_argument("--vivado", default=cfg["vivado"])
    ap.add_argument("--min-coverage", type=float, default=cfg["min_cov"],
                    help=f"SAIF annotation floor (default {cfg['min_cov']} from "
                         f"SAIF_MIN_COVERAGE). Below this, energy is omitted.")
    ap.add_argument("--clean-saif", dest="clean_saif", action="store_true",
                    default=cfg["clean_saif"],
                    help="reclaim /tmp/fsaif_<design>/ after both passes "
                         f"(default {cfg['clean_saif']} from CLEAN_SAIF_WORKDIRS)")
    ap.add_argument("--keep-saif-workdir", dest="clean_saif", action="store_false",
                    help="keep /tmp/fsaif_<design>/ for debugging")
    ap.add_argument("--use-dsp", type=int, choices=(0, 1), default=cfg["use_dsp"],
                    help="0 adds -max_dsp 0, keeping all multiplier arithmetic in "
                         "LUTs so throughput/area has a single currency. Run both.")
    ap.add_argument("--frames", type=int, default=cfg["frames"],
                    help=f"transform frames the power testbench runs "
                         f"(default {cfg['frames']} from SAIF_FRAMES)")
    ap.add_argument("--no-saif", action="store_true",
                    help="skip the SAIF pass. Area and timing stay valid; energy "
                         "and throughput/W are omitted, not guessed.")
    ap.add_argument("--tb", default=DEFAULT_TB)
    ap.add_argument("--source-dir", default=DEFAULT_SOURCE_DIR)
    ap.add_argument("--shared-dir", default=DEFAULT_SHARED_DIR)
    ap.add_argument("--generated-dir", default=DEFAULT_GENERATED_DIR)
    ap.add_argument("--work-dir", default=DEFAULT_WORK)
    ap.add_argument("--reports", default=DEFAULT_WORK)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--csv", default=DEFAULT_CSV)
    ap.add_argument("--cycles-file", default=DEFAULT_CYCLES)
    ap.add_argument("--timeout", type=int, default=cfg["timeout"],
                    help=f"per-pass Vivado timeout, two passes per design "
                         f"(default {cfg['timeout']} from VIVADO_TIMEOUT_S)")
    ap.add_argument("--implement", action="store_true",
                    help="run vivado_implement.tcl instead of vivado_synthesis_v2.tcl: "
                         "it SOURCES v2 unmodified, then places, phys-opts and routes "
                         "the same netlist and re-reports. Writes a second CSV per "
                         "design with post-synthesis and post-route side by side. "
                         "Output paths get an _impl suffix so the post-synthesis "
                         "sweep is not overwritten. Slower -- budget 5-15 min per "
                         "design on top of synthesis.")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    cfg["vivado"] = args.vivado

    if args.implement:
        # Keep the post-synthesis sweep intact: only paths the user left at their
        # default are redirected, so an explicit --out/--csv/--work-dir still wins.
        if args.work_dir == DEFAULT_WORK:
            args.work_dir = DEFAULT_WORK + "_impl"
        if args.reports == DEFAULT_WORK:
            args.reports = args.work_dir
        if args.out == DEFAULT_OUT:
            args.out = os.path.splitext(DEFAULT_OUT)[0] + "_impl.txt"
        if args.csv == DEFAULT_CSV:
            args.csv = os.path.splitext(DEFAULT_CSV)[0] + "_impl.csv"
        if args.timeout == cfg["timeout"]:
            args.timeout = max(cfg["timeout"], 5400)
        log("--implement: place + phys_opt + route after synthesis; "
            f"outputs under {args.work_dir}")
        if not os.path.isfile(IMPL_TCL):
            raise SystemExit(f"vivado_implement.tcl not found at {IMPL_TCL}")

    for n in args.sizes:
        if n & (n - 1) or n < 2 or n > 1024:
            raise SystemExit(f"{n} is not a power of two in [2, 1024]")

    os.makedirs(args.reports, exist_ok=True)

    if not args.report_only:
        for p, what in ((V2_TCL, "vivado_synthesis_v2.tcl"),
                        (SAIF_TCL, "generate_saif_funcsim.tcl")):
            if not os.path.isfile(p):
                raise SystemExit(f"{what} not found at {p}")
        if not os.path.isfile(args.tb):
            raise SystemExit(f"power testbench not found: {args.tb}")
        if not os.path.exists(args.vivado):
            raise SystemExit(f"vivado not found at {args.vivado}\n"
                             f"Pass --vivado /path/to/vivado")
        if not os.path.isfile(args.cycles_file):
            log(f"WARNING no cycle table at {args.cycles_file} -- throughput and "
                f"energy will be N/A. Run sim/fp16_performance_evaluator.py first.")

        log(f"part {args.part}  clock {args.clock_period} ns  use_dsp={args.use_dsp}")
        log(f"sizes {args.sizes}")
        for n in args.sizes:
            log(f"=== fp16_fft_{n} ===")
            synth_one(n, args, cfg)

    build_report(args.sizes, args, cfg)


if __name__ == "__main__":
    main()
