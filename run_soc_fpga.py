#!/usr/bin/env python3
"""
SoC-level FPGA PPA: PicoRV32 + FFT extension, synthesised as one design
=======================================================================
The throughput tables convert cycles to transforms per second using the FFT
CORE's maximum frequency, because the SoC had never been synthesised. That makes
the throughput column a conversion rather than a measurement: picorv32, the
32 KB memory and the PCPI handshake all contribute paths, and the real system
frequency is whichever of those is slowest.

This driver synthesises the whole SoC through the same unmodified
`vivado_synthesis_v2.tcl` and `generate_saif_funcsim.tcl` the three precision
tracks use, and reports:

  - SoC area, timing and power
  - the SoC's own maximum frequency, against the core's
  - SoC throughput recomputed at the SoC frequency, which is the measurement the
    cycle counts have been waiting for
  - how much of the SoC the FFT core accounts for

HOW IT FITS THE EXISTING FLOW WITHOUT FORKING IT
  Two obstacles, both handled outside the TCL:

  1. v2 derives its top as "${design_name}_top", which picorv32_fft_soc can
     never match. `risc-v-integration/soc_fft_top.v` is a wrapper that does
     match, passed as the top file.

  2. generate_saif_funcsim.tcl passes a fixed define list and runs xsim in its
     own scratch directory, so the firmware path can reach the testbench neither
     as a define nor as a relative path. This driver renders
     `tb_soc_power.v` from `tb_soc_power.v.in` with an absolute firmware path
     substituted in.

THE FAILURE MODE TO WATCH
  picorv32_fft_soc has no output ports. Every signal inside is unobservable from
  the boundary, so synthesis is entitled to delete the whole subsystem: a CPU
  writing a memory nobody reads is not required to exist. The wrapper carries
  DONT_TOUCH to prevent that, but the attribute's behaviour here is unverified.
  A plausibility floor therefore guards the result: a LUT count below
  --min-luts, or zero BRAM, is reported as PRUNED and the numbers are withheld
  rather than published as a SoC.

Usage (from the repository root):
    python3 run_soc_fpga.py                      # synthesise + SAIF power
    python3 run_soc_fpga.py --implement          # place, phys-opt and route too
    python3 run_soc_fpga.py --no-saif            # area and timing only
    python3 run_soc_fpga.py --report-only        # re-tabulate

Outputs `synth_mixed/soc_fpga_report.txt`, `.csv` and `.log`.

STATUS: NOT RUN AGAINST VIVADO. The wrapper and the generated testbench both
  elaborate and simulate under Icarus Verilog against the real firmware (512
  results over 2 frames). Everything Vivado-specific is unverified: DONT_TOUCH
  on a subsystem with no outputs, BRAM inference on the 32 KB byte-enabled
  memory, and whether a 32 KB memory plus a CPU plus the FFT core fits an
  XC7A35T at all. The first run settles all three, and the guard above is there
  because the most likely wrong answer looks like a plausible small one.
"""

import argparse
import csv as csv_mod
import glob
import os
import re
import shutil
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
RVI = os.path.join(REPO_ROOT, "risc-v-integration")

V2_TCL = os.path.join(REPO_ROOT, "vivado_synthesis_v2.tcl")
SAIF_TCL = os.path.join(REPO_ROOT, "generate_saif_funcsim.tcl")
IMPL_TCL = os.path.join(REPO_ROOT, "vivado_implement.tcl")

TB_TEMPLATE = os.path.join(RVI, "tb_soc_power.v.in")
SOC_TOP = os.path.join(RVI, "soc_fft_top.v")
SOC_RTL = os.path.join(RVI, "picorv32_fft_soc.v")
PCPI_RTL = os.path.join(RVI, "fft_pcpi_wrapper.v")
PICORV32 = os.path.join(REPO_ROOT, "picorv32.v")
SEL_CSV = os.path.join(REPO_ROOT, "synth_mixed", "mixed_selected_chromosomes.csv")
SOC_CYCLES = os.path.join(REPO_ROOT, "synth_mixed", "soc_throughput.csv")
MIXED_METRICS = os.path.join(REPO_ROOT, "synth_mixed", "mixed_fpga_metrics.csv")

DEFAULT_WORK = os.path.join(REPO_ROOT, "synth_mixed", "soc_fpga_work")
DEFAULT_OUT = os.path.join(REPO_ROOT, "synth_mixed", "soc_fpga_report.txt")
DEFAULT_CSV = os.path.join(REPO_ROOT, "synth_mixed", "soc_fpga_metrics.csv")

DESIGN = "soc_fft"
STRIP_PATH = "tb_soc_power/uut"

CHECKSUM_RE = re.compile(r"Synth Design complete\s*\|\s*Checksum:\s*(\S+)")
SAIF_NETS_RE = re.compile(r"Design nets matched\s*=\s*(\d+)\s+of\s+(\d+)")


def log(msg):
    print(f"[soc-fpga] {msg}", flush=True)


def fnum(d, k):
    try:
        return float(d[k])
    except (KeyError, TypeError, ValueError):
        return None


def read_globals():
    d = {"vivado": "/home/digital-1/2025.2/Vivado/bin/vivado",
         "clock": 10.0, "part": "xc7a35tcpg236-1", "frames": 2,
         "use_dsp": 1, "min_cov": 0.10, "timeout": 5400}
    p = os.path.join(REPO_ROOT, "globalVariablesMixedFFT.py")
    if not os.path.exists(p):
        return d
    t = open(p, encoding="utf-8", errors="replace").read()
    for key, pat, cast in (("vivado", r"^VIVADO_PATH\s*=\s*['\"](.+?)['\"]", str),
                           ("clock", r"^POWER_CLOCK_NS\s*=\s*([0-9.]+)", float),
                           ("part", r"^FPGA_DEVICE\s*=\s*['\"](.+?)['\"]", str),
                           ("use_dsp", r"^USE_DSP\s*=\s*(\d+)", int),
                           ("min_cov", r"^SAIF_MIN_COVERAGE\s*=\s*([0-9.]+)", float)):
        m = re.search(pat, t, re.M)
        if m:
            d[key] = cast(m.group(1))
    return d


def parse_metrics_csv(path):
    if not os.path.exists(path):
        return None
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv_mod.reader(f):
            if len(r) == 2 and r[0] != "Metric":
                out[r[0]] = r[1]
    return out


def run_vivado(cmd, log_path, timeout):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, cwd=REPO_ROOT)
    except subprocess.TimeoutExpired:
        open(log_path, "w", encoding="utf-8").write(f"TIMEOUT after {timeout}s\n")
        return False, timeout, ""
    dt = time.time() - t0
    out = (r.stdout or "") + "\n===== STDERR =====\n" + (r.stderr or "")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"cmd: {' '.join(cmd)}\nreturncode: {r.returncode}\n\n{out}")
    if r.returncode != 0:
        for ln in [l for l in (r.stdout or "").splitlines()
                   if "ERROR" in l or "CRITICAL" in l][:12]:
            log(f"    {ln.strip()}")
    return r.returncode == 0, dt, out


# ---------------------------------------------------------------------------
def selected_chromosome(n):
    if not os.path.isfile(SEL_CSV):
        raise SystemExit(f"{SEL_CSV} not found -- run run_mixed_fpga.py --select-only")
    with open(SEL_CSV, newline="", encoding="utf-8") as f:
        for r in csv_mod.DictReader(f):
            if r.get("N") == str(n):
                return r["chromosome"], r.get("solution_id", "?")
    raise SystemExit(f"no chromosome for N={n} in {SEL_CSV}")


def build_core(n, chrom, dest):
    sys.path.insert(0, REPO_ROOT)
    from fft_template_generator import FFTTemplateGenerator
    os.makedirs(dest, exist_ok=True)
    gen = FFTTemplateGenerator(fft_size=n)
    if len(chrom) != gen.get_chromosome_length():
        raise SystemExit(f"chromosome {chrom} is {len(chrom)} bits, generator "
                         f"wants {gen.get_chromosome_length()}")
    core, top = gen.generate_verilog(chrom, os.path.join(dest, f"mixed_fft_{n}.v"))
    if "butterfly_wrapper_gated" not in open(core, encoding="utf-8",
                                             errors="replace").read():
        raise SystemExit(f"{core} is UNGATED; the SoC would measure a different "
                         f"architecture than the paper reports")
    return core, top


def build_firmware(n, num_tests, work):
    sys.path.insert(0, RVI)
    sys.path.insert(0, REPO_ROOT)
    from rv32i_asm import assemble
    from performance_evaluator import PerformanceEvaluator
    asm = os.path.join(RVI, "fft_batch_test.asm")
    text = open(asm, encoding="utf-8", errors="replace").read()
    if "<<<<<<< " in text or ">>>>>>> " in text:
        raise SystemExit(f"{asm} has unresolved merge conflict markers")
    words = assemble(text.splitlines(True))
    pe = PerformanceEvaluator(n)
    fw = ["00000013"] * 8192
    for i, w in enumerate(words):
        fw[i] = f"{w:08x}"
    for ti, vec in enumerate(pe.test_vectors[:num_tests]):
        for si, s in enumerate(vec):
            re8 = pe.float_to_fp8_e4m3(s.real) & 0xFF
            im8 = pe.float_to_fp8_e4m3(s.imag) & 0xFF
            fw[2048 + ti * n + si] = f"{(re8 << 8) | im8:08x}"
    path = os.path.join(work, "firmware.hex")
    open(path, "w").write("\n".join(fw) + "\n")
    pe._write_twiddle_file(work)
    return os.path.abspath(path), len(words)


def render_tb(work, firmware_abs, clock_ns, max_frames, timeout_cycles):
    if not os.path.isfile(TB_TEMPLATE):
        raise SystemExit(f"{TB_TEMPLATE} not found")
    t = open(TB_TEMPLATE, encoding="utf-8").read()
    for k, v in (("@MAX_FRAMES@", str(max_frames)),
                 ("@CLOCK_NS@", f"{clock_ns}"),
                 ("@FIRMWARE_HEX@", firmware_abs.replace("\\", "/")),
                 ("@TIMEOUT_CYCLES@", str(timeout_cycles))):
        t = t.replace(k, v)
    left = re.findall(r"@[A-Z_]+@", t)
    if left:
        raise SystemExit(f"unsubstituted placeholders in the testbench: {left}")
    path = os.path.join(work, "tb_soc_power.v")
    open(path, "w", encoding="utf-8").write(t)
    return os.path.abspath(path)


def stage_sources(work, core_top, twiddle_dir):
    """One flat directory, because both TCLs glob exactly one. The FFT core and
    soc_fft_top stay OUT of it: the scripts add those by path, and a file both
    globbed and added by path is a duplicate module declaration."""
    srcs = os.path.join(work, "srcs")
    os.makedirs(srcs, exist_ok=True)
    for f in glob.glob(os.path.join(srcs, "*.v")):
        os.remove(f)
    for f in glob.glob(os.path.join(REPO_ROOT, "verilog_sources", "*.v")):
        shutil.copy2(f, srcs)
    for f in (SOC_RTL, PCPI_RTL, PICORV32, core_top):
        if not os.path.isfile(f):
            raise SystemExit(f"required source missing: {f}")
        shutil.copy2(f, srcs)

    # The repository's twiddle_rom.v carries an absolute /home/digital-1 path,
    # written in place by soc_evaluator.py. Patch the COPY so this works on any
    # machine; verilog_sources is never written to.
    tw = os.path.join(srcs, "twiddle_rom.v")
    if os.path.isfile(tw):
        s = open(tw, encoding="utf-8", errors="replace").read()
        tw_file = os.path.join(twiddle_dir, "twiddles_1024.txt").replace("\\", "/")
        s2 = re.sub(r'(\$readmem[bh]\s*\(\s*)"[^"]*twiddles_1024\.txt"',
                    r'\1"%s"' % tw_file, s)
        if s2 != s:
            open(tw, "w", encoding="utf-8").write(s2)
            log("patched the twiddle path in the staged copy of twiddle_rom.v")
    return srcs


# ---------------------------------------------------------------------------
def synth(args, cfg, core, core_top, work):
    srcs = stage_sources(work, core_top, os.path.abspath(work))
    fw, ninsn = build_firmware(args.fft_n, args.frames_fw, work)
    log(f"firmware: {ninsn} instructions, {args.frames_fw} test vectors")
    tb = render_tb(work, fw, args.clock_period, args.frames_fw,
                   args.sim_timeout_cycles)
    saif = os.path.join(work, f"{DESIGN}.saif")
    csv_out = os.path.join(args.reports, f"{DESIGN}_metrics.csv")
    os.makedirs(args.reports, exist_ok=True)

    saif_ok, saif_cksum = False, None
    if not args.no_saif:
        cmd = [cfg["vivado"], "-mode", "batch", "-source", SAIF_TCL, "-nojournal",
               "-log", os.path.join(work, "saif_vivado.log"), "-tclargs",
               DESIGN, os.path.abspath(core), os.path.abspath(SOC_TOP),
               os.path.abspath(srcs), tb, os.path.abspath(saif),
               str(args.fft_n), str(args.frames), args.part,
               str(args.clock_period)]
        saif_ok, dt, out = run_vivado(cmd, os.path.join(work, "saif_run.log"),
                                      args.timeout)
        m = CHECKSUM_RE.search(out or "")
        saif_cksum = m.group(1) if m else None
        log(f"  SAIF pass: {'ok' if saif_ok else 'FAILED'} ({dt:.0f}s)  "
            f"checksum={saif_cksum}")
        if saif_ok and not os.path.isfile(saif):
            log("  SAIF pass returned ok but wrote no SAIF")
            saif_ok = False

    tcl = IMPL_TCL if args.implement else V2_TCL
    cmd = [cfg["vivado"], "-mode", "batch", "-source", tcl, "-nojournal",
           "-log", os.path.join(work, "pwr_vivado.log"), "-tclargs",
           DESIGN, os.path.abspath(csv_out), str(args.clock_period),
           os.path.abspath(core), os.path.abspath(SOC_TOP),
           os.path.abspath(srcs), args.part,
           (os.path.abspath(saif) if saif_ok else ""),
           str(args.use_dsp), STRIP_PATH]
    if args.implement:
        cmd.append(os.path.abspath(os.path.splitext(csv_out)[0] + "_impl.csv"))
    ok, dt, out = run_vivado(cmd, os.path.join(work, "pwr_run.log"), args.timeout)
    m = CHECKSUM_RE.search(out or "")
    pwr_cksum = m.group(1) if m else None
    cov = SAIF_NETS_RE.search(out or "")
    log(f"  power pass: {'ok' if ok else 'FAILED'} ({dt:.0f}s)  "
        f"checksum={pwr_cksum}")

    prov = {"synth_ok": int(ok), "saif_pass_ok": int(saif_ok),
            "saif_checksum": saif_cksum or "", "pwr_checksum": pwr_cksum or "",
            "checksum_match": int(bool(saif_cksum) and saif_cksum == pwr_cksum)}
    if cov:
        matched, total = int(cov.group(1)), int(cov.group(2))
        prov["saif_nets_matched"] = matched
        prov["saif_nets_total"] = total
        prov["saif_coverage"] = round(matched / total, 4) if total else 0.0
    with open(os.path.join(args.reports, f"{DESIGN}_driver.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv_mod.writer(f)
        w.writerow(["Metric", "Value"])
        for k, v in prov.items():
            w.writerow([k, v])
    return ok


# ---------------------------------------------------------------------------
def soc_cycles():
    """Cycle counts from the SoC simulation, which are what the SoC frequency
    gets applied to. Measured, and independent of synthesis."""
    d = parse_metrics_csv(SOC_CYCLES)
    if not d:
        return {}
    out = {}
    for k_out, k_in in (("total", "cycles_per_transform_total"),
                        ("accel", "cycles_per_transform_accelerator"),
                        ("datapath", "cycles_per_transform_datapath")):
        v = fnum(d, k_in)
        if v:
            out[k_out] = v
    return out


def core_fmax(n):
    """The FFT core's standalone f_max, for the overstatement column. Read with
    DictReader, not parse_metrics_csv: mixed_fpga_metrics.csv is a wide
    one-row-per-size table, not a two-column Metric/Value file."""
    if not os.path.isfile(MIXED_METRICS):
        return None
    with open(MIXED_METRICS, newline="", encoding="utf-8") as f:
        for r in csv_mod.DictReader(f):
            if r.get("N") == str(n):
                try:
                    return float(r["fmax_mhz"])
                except (KeyError, ValueError):
                    return None
    return None


def build_report(args):
    m = parse_metrics_csv(os.path.join(args.reports, f"{DESIGN}_metrics.csv"))
    if m is None:
        log(f"no {DESIGN}_metrics.csv under {args.reports}; nothing to tabulate")
        return
    side = parse_metrics_csv(os.path.join(args.reports, f"{DESIGN}_driver.csv"))
    if side:
        m.update(side)
    impl = parse_metrics_csv(os.path.join(args.reports, f"{DESIGN}_metrics_impl.csv"))
    routed = bool(impl and str(impl.get("route_ran")) == "1")

    def g(k, fmt="{:.0f}", dash="-", src=None):
        v = fnum(src if src is not None else m, k)
        return dash if v is None else fmt.format(v)

    luts = fnum(m, "lut_count")
    bram = fnum(m, "bram_count")
    pruned = (luts is not None and luts < args.min_luts) or (bram == 0)

    # the SAIF gate, same two tests as the precision tracks
    pdyn, pvl = fnum(m, "dynamic_power_w"), fnum(m, "dynamic_power_vectorless_w")
    shift = (abs(pdyn - pvl) / abs(pvl)
             if (pdyn is not None and pvl not in (None, 0.0)) else None)
    cov = fnum(m, "saif_coverage")
    by_cov = cov is not None and cov >= args.min_coverage
    by_shift = shift is not None and shift >= args.min_power_shift
    trusted = bool(by_cov or by_shift)

    soc_fmax_v = fnum(impl, "impl_fmax_mhz") if routed else fnum(m, "fmax_mhz")
    soc_lut = fnum(impl, "impl_lut_count") if routed else luts
    soc_pdyn = fnum(impl, "impl_dynamic_power_w") if routed else pdyn
    cyc = soc_cycles()
    cf = core_fmax(args.fft_n)

    L = ["=" * 78,
         "PICORV32 + FFT SoC - FPGA PPA, SYNTHESISED AS ONE DESIGN",
         "=" * 78, "",
         f"Design name            : {DESIGN} (top module soc_fft_top)",
         f"FFT core inside        : mixed_fft_{args.fft_n}, gated",
         f"Unified memory         : {args.mem_words} words x 32 bits "
         f"= {args.mem_words * 4 // 1024} KB",
         f"Target FPGA device     : {args.part}",
         f"Clock period for power : {args.clock_period} ns",
         f"Stage reported         : "
         + ("post-route (placed, phys-opt, routed)" if routed
            else "post-synthesis (synth_design + opt_design)"),
         f"Synthesis script       : "
         + ("vivado_implement.tcl, which sources vivado_synthesis_v2.tcl"
            if args.implement else "vivado_synthesis_v2.tcl (unmodified)"),
         ""]

    if pruned:
        L += ["!" * 78,
              "RESULT WITHHELD - THE DESIGN LOOKS PRUNED",
              "!" * 78, "",
              f"  Logic LUTs reported : {g('lut_count')} "
              f"(floor for a plausible SoC: {args.min_luts})",
              f"  Block RAM tiles     : {g('bram_count')}",
              "",
              "  picorv32_fft_soc has no output ports, so nothing inside it is",
              "  observable from the boundary and synthesis may legally delete the",
              "  whole subsystem. soc_fft_top carries DONT_TOUCH to prevent that; a",
              "  count this low means it did not hold on this Vivado version.",
              "",
              "  Do not quote these numbers. Open the utilisation report under",
              f"  {args.reports} and check whether picorv32 and the memory are",
              "  present at all. If they are gone, the fix is observability: give",
              "  picorv32_fft_soc a real output (a registered reduction of mem_rdata,",
              "  say) rather than relying on an attribute.", ""]

    COLS = [("Quantity", "what is measured"), ("Value", "with unit")]
    rows = [
        ("Logic LUTs", g("lut_count", "{:,.0f}") + " count"),
        ("LUTs used as memory", g("lutram_count", "{:,.0f}") + " count"),
        ("Flip-flops", g("ff_count", "{:,.0f}") + " count"),
        ("Block RAM tiles", g("bram_count", "{:,.0f}") + " count"),
        ("DSP slices", g("dsp_count", "{:,.0f}") + " count"),
        ("Critical path delay", g("critical_path_delay_ns", "{:.3f}") + " ns"),
        ("Worst negative slack", g("wns_ns", "{:+.3f}") + " ns"),
        ("Maximum frequency", g("fmax_mhz", "{:.2f}") + " MHz"),
        ("Dynamic power", g("dynamic_power_w", "{:.4f}") + " W"),
        ("Static power", g("static_power_w", "{:.4f}") + " W"),
        ("Total on-chip power", g("total_power_w", "{:.4f}") + " W"),
        ("Vectorless dynamic power", g("dynamic_power_vectorless_w", "{:.4f}")
         + " W, for reference"),
        ("Dynamic power shift from vectorless",
         ("{:.1f}".format(100 * shift) if shift is not None else "-") + " percent"),
        ("Design nets annotated",
         ("{:.0f}".format(100 * cov) if cov is not None else "-") + " percent"),
        ("Switching activity trusted", ("yes" if trusted else "no")
         + (f" (on {'coverage' if by_cov else 'power shift'})" if trusted else "")),
        ("Netlist checksums agree",
         {"1": "yes", "0": "no"}.get(str(m.get("checksum_match")), "?")),
    ]
    if routed:
        rows += [
            ("Logic LUTs, post-route", g("impl_lut_count", "{:,.0f}", src=impl) + " count"),
            ("Maximum frequency, post-route", g("impl_fmax_mhz", "{:.2f}", src=impl) + " MHz"),
            ("Dynamic power, post-route", g("impl_dynamic_power_w", "{:.4f}", src=impl) + " W"),
        ]
    w0 = max(len(COLS[0][0]), len(COLS[0][1]), max(len(r[0]) for r in rows))
    w1 = max(len(COLS[1][0]), len(COLS[1][1]), max(len(r[1]) for r in rows))
    L += ["TABLE 1  SoC RESOURCES, TIMING AND POWER",
          f"{COLS[0][0].ljust(w0)}  {COLS[1][0].ljust(w1)}",
          f"{COLS[0][1].ljust(w0)}  {COLS[1][1].ljust(w1)}",
          "-" * (w0 + w1 + 2)]
    for a, b in rows:
        L.append(f"{a.ljust(w0)}  {b.ljust(w1)}")

    # ---- the point of the exercise ----------------------------------------
    if cyc and soc_fmax_v:
        T = [("Measurement basis", "which cycles are counted"),
             ("Cycles per transform", "clock cycles"),
             ("Throughput at the SoC frequency", "transforms per second"),
             ("Throughput at the core frequency", "transforms per second"),
             ("Overstated by", "percent")]
        body = []
        for label, key in (("Datapath only", "datapath"),
                           ("Accelerator occupied", "accel"),
                           ("End to end", "total")):
            c = cyc.get(key)
            if not c:
                continue
            soc_t = soc_fmax_v * 1e6 / c
            core_t = (cf * 1e6 / c) if cf else None
            over = (100 * (core_t - soc_t) / soc_t) if core_t else None
            body.append([label, f"{c:,.0f}", f"{soc_t:,.0f}",
                         f"{core_t:,.0f}" if core_t else "-",
                         f"{over:+.1f}" if over is not None else "-"])
        if body:
            ws = [max(len(T[i][0]), len(T[i][1]),
                      max(len(b[i]) for b in body)) for i in range(len(T))]
            L += ["", "TABLE 2  SoC THROUGHPUT, NOW A MEASUREMENT",
                  "  ".join(t[0].ljust(w) for t, w in zip(T, ws)),
                  "  ".join(t[1].ljust(w) for t, w in zip(T, ws)),
                  "-" * (sum(ws) + 2 * (len(ws) - 1))]
            for b in body:
                L.append("  ".join(v.ljust(w) for v, w in zip(b, ws)))
            L += ["",
                  "  Cycle counts come from the SoC simulation and do not depend on",
                  "  synthesis. The two throughput columns apply the SoC's own frequency",
                  "  and the FFT core's standalone frequency to the same counts; the last",
                  "  column is how much the core frequency overstates the system, which is",
                  "  the error the earlier tables carried."]

    if soc_lut and cf is not None:
        core_lut = None
        if os.path.isfile(MIXED_METRICS):
            with open(MIXED_METRICS, newline="", encoding="utf-8") as f:
                for r in csv_mod.DictReader(f):
                    if r.get("N") == str(args.fft_n):
                        core_lut = fnum(r, "lut_count")
        if core_lut:
            L += ["", "TABLE 3  HOW MUCH OF THE SoC IS THE FFT",
                  f"  FFT core logic LUTs                      {core_lut:>10,.0f} count",
                  f"  SoC logic LUTs                           {soc_lut:>10,.0f} count",
                  f"  FFT core share of SoC logic              "
                  f"{100 * core_lut / soc_lut:>10.1f} percent",
                  "",
                  "  The remainder is picorv32, the unified memory's address and",
                  "  byte-enable logic, and the PCPI handshake."]

    if not trusted:
        L += ["", "WARNING  Switching activity was not annotated to either test's",
              "         satisfaction, so the dynamic power above is effectively",
              "         vectorless and should not be used for an energy figure."]
    if str(m.get("checksum_match")) == "0":
        L += ["", "WARNING  The two synthesis passes produced different netlists, so",
              "         the activity file describes a design other than the one",
              "         annotated. Do not trust the power numbers."]

    text = "\n".join(L) + "\n"
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    open(args.out, "w", encoding="utf-8").write(text)
    print("\n" + text)
    log(f"table: {args.out}")

    with open(args.csv, "w", newline="", encoding="utf-8") as f:
        w = csv_mod.writer(f)
        w.writerow(["Metric", "Value"])
        w.writerow(["design", DESIGN])
        w.writerow(["stage", "post-route" if routed else "post-synthesis"])
        w.writerow(["pruned_guard_tripped", int(bool(pruned))])
        w.writerow(["saif_power_shift", "" if shift is None else round(shift, 4)])
        w.writerow(["saif_trusted", int(trusted)])
        for k, v in sorted(m.items()):
            w.writerow([k, v])
        if impl:
            for k, v in sorted(impl.items()):
                w.writerow([k, v])
    log(f"csv  : {args.csv}")


# ---------------------------------------------------------------------------
class _Tee:
    """Duplicate everything printed to a log beside the report. Terminal
    scrollback is not a record."""

    def __init__(self, path):
        self.path, self.stream, self.stdout = path, None, None

    def __enter__(self):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            self.stream = open(self.path, "w", encoding="utf-8")
        except OSError:
            return self
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
        for t in (self.stdout, self.stream):
            if t is not None:
                try:
                    t.write(s)
                except OSError:
                    pass

    def flush(self):
        for t in (self.stdout, self.stream):
            if t is not None:
                try:
                    t.flush()
                except OSError:
                    pass


def main():
    cfg = read_globals()
    ap = argparse.ArgumentParser(
        description="Synthesise the PicoRV32 + FFT SoC through the same Vivado flow")
    ap.add_argument("--fft-n", type=int, default=256)
    ap.add_argument("--mem-words", type=int, default=8192,
                    help="unified memory depth in 32-bit words (default 8192 = "
                         "32 KB, matching what the SoC testbenches instantiate)")
    ap.add_argument("--clock-period", type=float, default=cfg["clock"])
    ap.add_argument("--part", default=cfg["part"])
    ap.add_argument("--vivado", default=cfg["vivado"])
    ap.add_argument("--use-dsp", type=int, choices=(0, 1), default=cfg["use_dsp"])
    ap.add_argument("--frames", type=int, default=cfg["frames"],
                    help="transforms the activity testbench exercises for the "
                         "SAIF (default 2; a netlist simulation of a CPU is slow "
                         "and the pattern repeats)")
    ap.add_argument("--frames-fw", type=int, default=11,
                    help="test vectors packed into the firmware image")
    ap.add_argument("--min-coverage", type=float, default=cfg["min_cov"])
    ap.add_argument("--min-power-shift", type=float, default=0.05)
    ap.add_argument("--min-luts", type=int, default=1500,
                    help="plausibility floor. Below this the design is reported "
                         "as pruned and the numbers withheld, because a SoC with "
                         "no output ports can be optimised away entirely.")
    ap.add_argument("--implement", action="store_true",
                    help="place, phys-opt and route as well, via vivado_implement.tcl")
    ap.add_argument("--no-saif", action="store_true",
                    help="skip the activity pass; area and timing stay valid and "
                         "power is left as vectorless, flagged as untrusted")
    ap.add_argument("--sim-timeout-cycles", type=int, default=2000000)
    ap.add_argument("--timeout", type=int, default=cfg["timeout"])
    ap.add_argument("--work-dir", default=DEFAULT_WORK)
    ap.add_argument("--reports", default=None)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--csv", default=DEFAULT_CSV)
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    cfg["vivado"] = args.vivado
    if args.reports is None:
        args.reports = args.work_dir
    if args.implement:
        if args.out == DEFAULT_OUT:
            args.out = os.path.splitext(DEFAULT_OUT)[0] + "_impl.txt"
        if args.csv == DEFAULT_CSV:
            args.csv = os.path.splitext(DEFAULT_CSV)[0] + "_impl.csv"
        if args.work_dir == DEFAULT_WORK:
            args.work_dir = DEFAULT_WORK + "_impl"
            args.reports = args.work_dir

    log_path = os.path.splitext(os.path.abspath(args.out))[0] + ".log"
    with _Tee(log_path):
        _run(args, cfg)


def _run(args, cfg):
    os.makedirs(args.work_dir, exist_ok=True)
    if not args.report_only:
        for p, what in ((V2_TCL, "vivado_synthesis_v2.tcl"),
                        (SAIF_TCL, "generate_saif_funcsim.tcl"),
                        (SOC_TOP, "soc_fft_top.v"),
                        (SOC_RTL, "picorv32_fft_soc.v"),
                        (PCPI_RTL, "fft_pcpi_wrapper.v"),
                        (PICORV32, "picorv32.v"),
                        (TB_TEMPLATE, "tb_soc_power.v.in")):
            if not os.path.isfile(p):
                raise SystemExit(f"{what} not found at {p}")
        if args.implement and not os.path.isfile(IMPL_TCL):
            raise SystemExit(f"vivado_implement.tcl not found at {IMPL_TCL}")
        if not os.path.exists(args.vivado):
            raise SystemExit(f"vivado not found at {args.vivado}\n"
                             f"Pass --vivado /path/to/vivado")
        chrom, sol = selected_chromosome(args.fft_n)
        log(f"FFT core: N={args.fft_n} chromosome {chrom} (solution {sol}), gated")
        core, core_top = build_core(args.fft_n, chrom,
                                    os.path.join(args.work_dir, "core"))
        log(f"part {args.part}  clock {args.clock_period} ns  "
            f"use_dsp={args.use_dsp}  memory {args.mem_words} words")
        if args.mem_words != 8192:
            log(f"NOTE --mem-words {args.mem_words} differs from the 8192 the SoC "
                f"testbenches use, so this is not the memory the cycle counts "
                f"were measured on")
        synth(args, cfg, core, core_top, args.work_dir)

    build_report(args)


if __name__ == "__main__":
    main()
