#!/usr/bin/env python3
"""Measure FFT throughput through the RISC-V PCPI interface."""

import argparse
import csv as csv_mod
import glob
import os
import re
import shutil
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
RVI = os.path.join(REPO_ROOT, "risc-v-integration")
SEL_CSV = os.path.join(REPO_ROOT, "synth_mixed", "mixed_selected_chromosomes.csv")
DEFAULT_TB = os.path.join(RVI, "tb_fft_soc_perf.v")
DEFAULT_OUT = os.path.join(REPO_ROOT, "synth_mixed", "soc_throughput.txt")

PERF_RE = re.compile(r"^PERF\s+(\S+)\s+(\S+)\s*$")


def log(msg):
    print(f"[soc-throughput] {msg}", flush=True)


def selected_chromosome(n):
    if not os.path.isfile(SEL_CSV):
        raise SystemExit(f"{SEL_CSV} not found -- run "
                         f"run_mixed_fpga.py --select-only first")
    with open(SEL_CSV, newline="", encoding="utf-8") as f:
        for r in csv_mod.DictReader(f):
            if r.get("N") == str(n):
                return r["chromosome"], r.get("solution_id", "?")
    raise SystemExit(f"no chromosome for N={n} in {SEL_CSV}")


def build_core(n, chrom, dest):
    """Regenerate the selected chromosome as mixed_fft_<N>, the name the PCPI
    wrapper instantiates, and refuse to continue if it is not gated."""
    sys.path.insert(0, REPO_ROOT)
    from fft_template_generator import FFTTemplateGenerator
    os.makedirs(dest, exist_ok=True)
    gen = FFTTemplateGenerator(fft_size=n)
    if len(chrom) != gen.get_chromosome_length():
        raise SystemExit(f"chromosome {chrom} is {len(chrom)} bits, "
                         f"generator wants {gen.get_chromosome_length()}")
    core, top = gen.generate_verilog(chrom, os.path.join(dest, f"mixed_fft_{n}.v"))
    src = open(core, encoding="utf-8", errors="replace").read()
    if "butterfly_wrapper_gated" not in src:
        raise SystemExit(f"{core} is UNGATED -- fft_template_generator.py is the "
                         f".ungated variant. The SoC would measure a different "
                         f"architecture than the paper reports.")
    return core, top


def build_firmware(n, num_tests, work):
    """Her assembler and her FP8 packing, used as-is. Deliberately does not
    import soc_evaluator, whose generate_bulletproof_wrapper() would overwrite
    fft_pcpi_wrapper.v as a side effect of importing nothing in particular."""
    sys.path.insert(0, RVI)
    sys.path.insert(0, REPO_ROOT)
    from rv32i_asm import assemble
    from performance_evaluator import PerformanceEvaluator

    asm = os.path.join(RVI, "fft_batch_test.asm")
    text = open(asm, encoding="utf-8", errors="replace").read()
    if "<<<<<<< " in text or ">>>>>>> " in text:
        raise SystemExit(f"{asm} still has unresolved merge conflict markers; "
                         f"the assembler cannot read it")
    words = assemble(text.splitlines(True))

    pe = PerformanceEvaluator(n)
    fw = ["00000013"] * 8192
    for i, w in enumerate(words):
        fw[i] = f"{w:08x}"
    base = 2048
    vecs = pe.test_vectors[:num_tests]
    for ti, vec in enumerate(vecs):
        for si, s in enumerate(vec):
            re8 = pe.float_to_fp8_e4m3(s.real) & 0xFF
            im8 = pe.float_to_fp8_e4m3(s.imag) & 0xFF
            fw[base + ti * n + si] = f"{(re8 << 8) | im8:08x}"
    open(os.path.join(work, "firmware.hex"), "w").write("\n".join(fw) + "\n")
    pe._write_twiddle_file(work)
    return len(words), len(vecs)


def stage_sources(work):
    """Local copies only. verilog_sources is never written to."""
    srcs = os.path.join(work, "srcs")
    os.makedirs(srcs, exist_ok=True)
    for f in glob.glob(os.path.join(REPO_ROOT, "verilog_sources", "*.v")):
        shutil.copy2(f, srcs)
    tw = os.path.join(srcs, "twiddle_rom.v")
    if os.path.isfile(tw):
        s = open(tw, encoding="utf-8", errors="replace").read()
        s2 = re.sub(r'(\$readmem[bh]\s*\(\s*)"[^"]*twiddles_1024\.txt"',
                    r'\1"twiddles_1024.txt"', s)
        if s2 != s:
            open(tw, "w", encoding="utf-8").write(s2)
            log("rewrote the twiddle path in the LOCAL copy of twiddle_rom.v "
                "(the repository's own copy carries an absolute digital-1 path)")
    return srcs


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
        description="Measure FFT throughput through the RISC-V PCPI interface")
    ap.add_argument("--sizes", type=int, default=256,
                    help="FFT size. Only 256 is supported today: "
                         "fft_batch_test.asm hardcodes 256 samples and 11 tests.")
    ap.add_argument("--num-tests", type=int, default=11)
    ap.add_argument("--fmax", type=float, default=None,
                    help="maximum frequency in MHz to quote throughput at. "
                         "Default reads it from synth_mixed/mixed_fpga_metrics.csv "
                         "for this size; without either, cycles only.")
    ap.add_argument("--clock-ns", type=float, default=10.0,
                    help="simulation clock, for the cycle-to-time conversion only")
    ap.add_argument("--tb", default=DEFAULT_TB)
    ap.add_argument("--work-dir", default=os.path.join(REPO_ROOT, "sim_soc_perf"))
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--keep-work", action="store_true")
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args()

    import socket
    _host = re.sub(r"[^A-Za-z0-9_-]", "", socket.gethostname().split(".")[0]) or "host"
    log_path = os.path.splitext(os.path.abspath(args.out))[0] + f".{_host}.log"
    with _Tee(log_path):
        _run(args)


def _run(args):

    n = args.sizes
    if n != 256:
        raise SystemExit("only --sizes 256 is supported: fft_batch_test.asm "
                         "hardcodes 256 samples and 11 tests. Another size needs "
                         "that firmware changed first.")
    for p, what in ((args.tb, "instrumented testbench"),
                    (os.path.join(REPO_ROOT, "picorv32.v"), "picorv32.v"),
                    (os.path.join(RVI, "picorv32_fft_soc.v"), "SoC top"),
                    (os.path.join(RVI, "fft_pcpi_wrapper.v"), "PCPI wrapper")):
        if not os.path.isfile(p):
            raise SystemExit(f"{what} not found at {p}")
    if shutil.which("iverilog") is None:
        raise SystemExit("iverilog not on PATH")

    work = args.work_dir
    if os.path.isdir(work):
        shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)

    chrom, sol = selected_chromosome(n)
    log(f"N={n} chromosome {chrom} (solution {sol}) -- the design the paper reports")
    core, top = build_core(n, chrom, os.path.join(work, "core"))
    log(f"regenerated gated core as {os.path.basename(top)}")

    ninsn, nvec = build_firmware(n, args.num_tests, work)
    log(f"firmware: {ninsn} instructions, {nvec} test vectors")
    srcs = stage_sources(work)

    others = [f for f in sorted(glob.glob(os.path.join(srcs, "*.v")))]
    cmd = ["iverilog", "-g2012", "-o", "soc.vvp", "-I", "srcs", "-I", "core",
           f"-DFFT_N={n}", f"-DNUM_TESTS={args.num_tests}",
           f"-DCLOCK_NS={args.clock_ns}",
           os.path.abspath(args.tb),
           os.path.join(RVI, "picorv32_fft_soc.v"),
           os.path.join(RVI, "fft_pcpi_wrapper.v"),
           core, top] + others + [os.path.join(REPO_ROOT, "picorv32.v")]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=work)
    if r.returncode != 0:
        print(r.stderr[:4000])
        raise SystemExit("iverilog compilation failed")
    log("compiled")

    try:
        r = subprocess.run(["vvp", "soc.vvp"], capture_output=True, text=True,
                           cwd=work, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"simulation exceeded {args.timeout}s")
    perf = {}
    for line in (r.stdout or "").splitlines():
        m = PERF_RE.match(line)
        if m:
            perf[m.group(1)] = m.group(2)
    open(os.path.join(work, "sim.log"), "w").write(r.stdout or "")
    if not perf:
        print((r.stdout or "")[-3000:])
        raise SystemExit("no PERF lines in the simulation output")

    def iv(k):
        try:
            return int(perf[k])
        except (KeyError, ValueError):
            return None

    frames = iv("frames_done") or 0
    if frames < args.num_tests:
        log(f"WARNING only {frames} of {args.num_tests} frames completed; the "
            f"per-transform figures below are averaged over those {frames}")
    if frames <= 0:
        raise SystemExit("no frame completed; nothing to report")

    total, accel = iv("cycles_total"), iv("cycles_accelerator")
    wait, load, store = iv("cycles_wait"), iv("cycles_load"), iv("cycles_store")
    start = iv("cycles_start")

    per = lambda v: (v / frames) if v is not None else None
    p_total, p_accel, p_wait = per(total), per(accel), per(wait)
    p_load, p_store, p_start = per(load), per(store), per(start)
    p_move = (p_accel - p_wait) if (p_accel and p_wait) else None

    fmax = args.fmax
    if fmax is None:
        mcsv = os.path.join(REPO_ROOT, "synth_mixed", "mixed_fpga_metrics.csv")
        if os.path.isfile(mcsv):
            with open(mcsv, newline="", encoding="utf-8") as f:
                for row in csv_mod.DictReader(f):
                    if row.get("N") == str(n):
                        try:
                            fmax = float(row["fmax_mhz"])
                        except (KeyError, ValueError):
                            pass
            if fmax:
                log(f"f_max {fmax:.2f} MHz read from mixed_fpga_metrics.csv")

    def thr(cyc):
        return (fmax * 1e6 / cyc) if (fmax and cyc) else None

    rows = [
        ("Datapath only (FFT core computing)", p_wait, thr(p_wait),
         "the CPU stalled in FFTWAIT; compare against ExecCycles"),
        ("Accelerator occupied (datapath + PCPI transfers)", p_accel, thr(p_accel),
         "cycles a custom-0 instruction holds the CPU"),
        ("End to end (application)", p_total, thr(p_total),
         "reset to last result in memory, CPU loops included"),
    ]
    COLS = [("Measurement basis", "which cycles are counted"),
            ("Cycles per transform", "clock cycles"),
            ("Throughput", "transforms per second"),
            ("What it counts", "definition")]
    body = [[r[0],
             "-" if r[1] is None else f"{r[1]:,.0f}",
             "-" if r[2] is None else f"{r[2]:,.0f}",
             r[3]] for r in rows]
    widths = [max(len(COLS[i][0]), len(COLS[i][1]),
                  max(len(b[i]) for b in body)) for i in range(4)]

    L = ["=" * 78,
         "FFT THROUGHPUT THROUGH THE RISC-V PCPI INTERFACE",
         "=" * 78, "",
         f"FFT size            : {n} points",
         f"Chromosome          : {chrom} (solution {sol}, gated)",
         f"Frames measured     : {frames} of {args.num_tests}",
         f"Simulation clock    : {args.clock_ns} ns",
         (f"Frequency quoted at : {fmax:.2f} MHz (standalone FFT core; the SoC "
          if fmax else "Frequency quoted at : not supplied; cycles only"),
         "",
         "",
         "  ".join(c[0].ljust(w) for c, w in zip(COLS, widths)),
         "  ".join(c[1].ljust(w) for c, w in zip(COLS, widths)),
         "-" * (sum(widths) + 6)]
    for b in body:
        L.append("  ".join(v.ljust(w) for v, w in zip(b, widths)))

    L += ["", "WHERE THE CYCLES GO, PER TRANSFORM",
          f"  {'Stage':<40} {'clock cycles':>13}   {'percent of end to end':>21}"]
    for label, v in (("FFT datapath", p_wait), ("Sample loads", p_load),
                     ("Result stores", p_store), ("Start instruction", p_start),
                     ("CPU loop overhead outside the extension",
                      (p_total - p_accel) if (p_total and p_accel) else None)):
        if v is not None and p_total:
            L.append(f"  {label:<40} {v:>13,.0f}   {100*v/p_total:>21.1f}")
    if p_move and p_wait:
        L += ["",
              f"  Data movement across PCPI costs {p_move:,.0f} cycles against "
              f"{p_wait:,.0f} for the",
              f"  computation itself -- a ratio of {p_move/p_wait:.2f} to 1."]
    if p_total and p_wait:
        L += [f"  The datapath is {100*p_wait/p_total:.1f} % of end-to-end time, so "
              f"{100*(1-p_wait/p_total):.1f} % is",
              "  spent getting samples in and out."]


    text = "\n".join(L) + "\n"
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    open(args.out, "w", encoding="utf-8").write(text)
    print("\n" + text)
    log(f"table: {args.out}")

    cpath = os.path.splitext(args.out)[0] + ".csv"
    with open(cpath, "w", newline="", encoding="utf-8") as f:
        w = csv_mod.writer(f)
        w.writerow(["Metric", "Value"])
        w.writerow(["fft_n", n])
        w.writerow(["chromosome", chrom])
        w.writerow(["fmax_mhz", fmax if fmax else ""])
        for k, v in sorted(perf.items()):
            w.writerow([k, v])
        for k, v in (("cycles_per_transform_total", p_total),
                     ("cycles_per_transform_accelerator", p_accel),
                     ("cycles_per_transform_datapath", p_wait),
                     ("cycles_per_transform_pcpi_movement", p_move)):
            w.writerow([k, "" if v is None else round(v, 2)])
    log(f"csv  : {cpath}")

    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    else:
        log(f"work kept at {work}")


if __name__ == "__main__":
    main()
