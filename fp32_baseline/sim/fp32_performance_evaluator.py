"""
Performance Evaluation Module -- FP32 Baseline Edition
======================================================
FP32 (IEEE 754 binary32) counterpart of performance_evaluator.py, so the FP32
baseline is scored with EXACTLY the same methodology as the mixed-precision
FP4/FP8 cores:

  * same 11 test signals (SIGNAL_LABELS / _generate_test_vectors unchanged)
  * same golden: FFT of the input quantised to the design's input format
  * same SQNR: golden re-quantised to the design's output format, then
        10*log10( mean|golden_q|^2 / mean|golden_q - rtl|^2 )
  * same averaging over signals, with an exact match counted as 100 dB
  * same testbench timing (load, start, wait for done, 3-cycle unload)

What differs is only what has to differ for an FP32 datapath:
  * input / output / twiddle format is FP32 (no FP8/FP4 conversions, no
    chromosome-driven precision selection)
  * 64-bit load / unload words ({real, imag}), 64-bit twiddle ROM
  * the golden FFT is computed in float64 before quantisation (numpy >= 2
    would otherwise run the FFT itself in float32 for complex64 input)
  * sources: fp32_baseline/source/*.v plus ONLY agu.v and
    bit_reversal.v from the shared ../verilog_sources (the rest of the mixed
    tree is not needed and its twiddle ROM hardcodes an absolute path)

Extra, reporting-only outputs (the 'sqnr' value itself is computed exactly as
in the mixed evaluator): per-signal SQNR, the number of exact signals, and the
average over finite signals only.  With FP32 several signals can come out
bit-exact, and the 100 dB cap then pulls the average DOWN -- report both.

Location: fp32_baseline/sim/fp32_performance_evaluator.py
All paths are resolved relative to this file, so it can be run from anywhere:

    python3 fp32_baseline/sim/fp32_performance_evaluator.py            # all sizes
    python3 fp32_baseline/sim/fp32_performance_evaluator.py --sizes 256 1024

or used from Python with the same interface as the mixed evaluator:

    ev = FP32PerformanceEvaluator(256)
    res = ev.evaluate_size()                      # or ev.evaluate_design(core_v, name)
"""

import argparse
import glob as glob_module
import math
import os
import re
import shutil
import struct
import subprocess
import zipfile

import numpy as np

SIGNAL_LABELS = [
    "Impulse",
    "Single Tone",
    "Multi-Tone",
    "Chirp (LFM)",
    "Sinusoid (complex)",
    "Step Function",
    "Gaussian Pulse",
    "Radar Pulsed Sinusoid",
    "Radar Clutter + Target",
    "Radar Barker-13 Pulse",
    "Radar Doppler Burst",
]

SIM_DIR_ROOT = os.path.dirname(os.path.abspath(__file__))       # fp32_baseline/sim
BASE_DIR = os.path.dirname(SIM_DIR_ROOT)                        # fp32_baseline
DEFAULT_SHARED_DIR = os.path.join(os.path.dirname(BASE_DIR), "verilog_sources")

FP32_MAX = float(np.finfo(np.float32).max)
INF_SQNR_CREDIT = 100.0          # same credit the mixed evaluator gives an exact match
ALL_SIZES = [2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]


class FP32PerformanceEvaluator:
    def __init__(self, fft_size, shared_sources_dir=DEFAULT_SHARED_DIR,
                 sim_dir=None, sim_timeout=600, dump_vcd=False):
        self.fft_size            = fft_size
        self.num_stages          = int(math.log2(fft_size))
        self.verilog_sources_dir = os.path.join(BASE_DIR, 'source')
        self.shared_sources_dir  = os.path.abspath(shared_sources_dir)
        self.sim_dir             = os.path.abspath(sim_dir or os.path.join(SIM_DIR_ROOT, 'perf'))
        self.sim_timeout         = sim_timeout
        # Off by default: this does not change anything about the SQNR/cycle
        # measurement this class exists for. When True, _generate_testbench
        # also dumps a VCD of the RTL simulation (same stimulus, same DUT),
        # for run_fp32_synthesis.py to feed into OpenSTA's `read_vcd` as a
        # real-activity replacement for the flat 0.2 toggle-rate guess.
        self.dump_vcd            = dump_vcd
        self.test_vectors        = self._generate_test_vectors()
        self.golden_outputs      = self._compute_golden_outputs()

    # ------------------------------------------------------------------
    # Test vectors -- IDENTICAL to performance_evaluator.py
    # ------------------------------------------------------------------
    def _generate_test_vectors(self):
        n     = self.fft_size
        n_arr = np.arange(n, dtype=np.float32)
        vecs  = []

        # 1. Impulse
        v = np.zeros(n, dtype=np.complex64)
        v[0] = 0.9 + 0.0j
        vecs.append(v)

        # 2. Single tone
        k = max(1, n // 8)
        v = (0.9 * np.cos(2.0 * np.pi * k * n_arr / n)).astype(np.complex64)
        vecs.append(v)

        # 3. Multi-tone
        tones = [1, 3, 5, 7] if n >= 8 else [1]
        v = np.zeros(n, dtype=np.complex64)
        for kt in tones:
            if kt < n // 2:
                v += np.exp(2j * np.pi * kt * n_arr / n).astype(np.complex64)
        peak = np.max(np.abs(v))
        v = (v / peak * 0.9).astype(np.complex64) if peak > 0 else v
        vecs.append(v)

        # 4. Chirp (LFM)
        v = (np.exp(1j * np.pi * (n_arr ** 2) / n) * 0.9).astype(np.complex64)
        vecs.append(v)

        # 5. Sinusoid (complex)
        k5 = max(1, n // 4)
        v  = (0.9 * np.exp(2j * np.pi * k5 * n_arr / n)).astype(np.complex64)
        vecs.append(v)

        # 7. Step function
        v = np.zeros(n, dtype=np.complex64)
        v[:n // 2] =  0.9 + 0.0j
        v[n // 2:] = -0.9 + 0.0j
        vecs.append(v)

        # 8. Gaussian pulse
        sigma  = np.float32(n / 8.0)
        centre = np.float32(n / 2.0)
        v = np.exp(-0.5 * ((n_arr - centre) / sigma) ** 2).astype(np.complex64)
        peak = np.max(np.abs(v))
        v = (v / peak * 0.9).astype(np.complex64) if peak > 0 else v
        vecs.append(v)

        # 9. Radar Pulsed Sinusoid
        k_radar = max(1, n // 6)
        pulse_start = n // 4
        pulse_end   = 3 * n // 4
        v = np.zeros(n, dtype=np.complex64)
        v[pulse_start:pulse_end] = np.exp(
            2j * np.pi * k_radar * n_arr[pulse_start:pulse_end] / n
        ).astype(np.complex64)
        peak = np.max(np.abs(v))
        v = (v / peak * 0.9).astype(np.complex64) if peak > 0 else v
        vecs.append(v)

        # 10. Radar Clutter + Target
        k_clutter = max(1, n // 16)
        k_target  = max(k_clutter + 2, n // 5)
        clutter_amp = np.float32(0.85)
        target_amp  = np.float32(0.05)
        v = (
            clutter_amp * np.exp(2j * np.pi * k_clutter * n_arr / n)
            + target_amp * np.exp(2j * np.pi * k_target  * n_arr / n)
        ).astype(np.complex64)
        peak = np.max(np.abs(v))
        v = (v / peak * 0.9).astype(np.complex64) if peak > 0 else v
        vecs.append(v)

        # 11. Radar Barker-13 Pulse
        barker13 = np.array([1, 1, 1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1], dtype=np.float32)
        v = np.zeros(n, dtype=np.complex64)
        code_len = min(13, n)
        v[:code_len] = barker13[:code_len]
        peak = np.max(np.abs(v))
        v = (v / peak * 0.9).astype(np.complex64) if peak > 0 else v
        vecs.append(v)

        # 12. Radar Doppler Burst
        k_doppler = max(1, n // 7)
        hamming   = np.hamming(n).astype(np.float32)
        v = (hamming * np.exp(2j * np.pi * k_doppler * n_arr / n)).astype(np.complex64)
        peak = np.max(np.abs(v))
        v = (v / peak * 0.9).astype(np.complex64) if peak > 0 else v
        vecs.append(v)

        assert len(vecs) == len(SIGNAL_LABELS), "Signal mapping count tracking mismatched"
        return vecs

    # ------------------------------------------------------------------
    # Golden: FFT of the FP32-quantised input (computed in float64)
    # ------------------------------------------------------------------
    def _compute_golden_outputs(self):
        goldens = []
        for v in self.test_vectors:
            v_quantized = np.array(
                [self.fp32_to_float(self.float_to_fp32(x.real)) +
                 1j * self.fp32_to_float(self.float_to_fp32(x.imag)) for x in v],
                dtype=np.complex128)
            goldens.append(np.fft.fft(v_quantized))
        return goldens

    # ==================================================================
    # Float <-> FP32 helpers (round-to-nearest-even, saturating, like the RTL)
    # ==================================================================
    @staticmethod
    def float_to_fp32(val):
        val = float(val)
        if val == 0.0:
            return 0
        if abs(val) > FP32_MAX:
            val = math.copysign(FP32_MAX, val)
        bits = struct.unpack('>I', struct.pack('>f', val))[0]
        # round-to-nearest may still produce Inf just above FP32_MAX: saturate
        if (bits & 0x7FFFFFFF) == 0x7F800000:
            bits = (bits & 0x80000000) | 0x7F7FFFFF
        return 0 if bits == 0x80000000 else bits      # the design emits +0

    @staticmethod
    def fp32_to_float(bits):
        return struct.unpack('>f', struct.pack('>I', bits & 0xFFFFFFFF))[0]

    # ------------------------------------------------------------------
    # Testbench -- same timing as the mixed evaluator, 64-bit words
    # ------------------------------------------------------------------
    def vcd_path(self, design_name):
        return os.path.join(self.sim_dir, f'{self._sanitize_name(design_name)}.vcd')

    def _generate_testbench(self, dut_file, design_name):
        design_name = self._sanitize_name(design_name)
        n          = self.fft_size
        addr_bits  = int(math.log2(n))
        num_tests  = len(self.test_vectors)
        top_module = f"{design_name}_top"

        butterflies     = (n // 2) * self.num_stages
        cycles_per_fft  = n + butterflies + 50
        ready_timeout   = 512 + cycles_per_fft
        watchdog_ns     = (1024 + num_tests * (cycles_per_fft + n * 5) + 1000) * 10

        sim_dir  = self.sim_dir
        out_path = os.path.join(sim_dir, f'{design_name}_output.txt')

        vec_hex_lines = []
        for ti, vec in enumerate(self.test_vectors):
            for si, sample in enumerate(vec):
                word = (self.float_to_fp32(sample.real) << 32) | self.float_to_fp32(sample.imag)
                vec_hex_lines.append(f"        tv[{ti*n + si}] = 64'h{word:016x};")
        vec_init = '\n'.join(vec_hex_lines)

        # Same stimulus/timing as the SQNR run, just also dumping a VCD when
        # asked -- so the activity that later gets fed into OpenSTA's
        # `read_vcd` comes from the exact same 11 representative signals
        # (Impulse, Chirp, radar bursts, ...), not a flat guess.
        dump_block = ""
        if self.dump_vcd:
            dump_block = (
                f'    initial begin\n'
                f'        $dumpfile("{self.vcd_path(design_name)}");\n'
                f'        $dumpvars(0, tb_{design_name});\n'
                f'    end\n'
            )

        tb = f"""\
`timescale 1ns/1ps
module tb_{design_name};
    reg clk; reg rst; reg start; wire done;
    reg load_en; reg [{addr_bits-1}:0] load_addr; reg [63:0] load_data;
    reg unload_en; reg [{addr_bits-1}:0] unload_addr; wire [63:0] unload_data;
    integer i, ti, out_file;
    integer cycle_count, total_cycles, load_cycles, unload_cycles_cnt;
    reg [63:0] tv [{num_tests*n - 1}:0];

    {top_module} dut (
        .clk(clk), .rst(rst), .start(start), .done(done),
        .load_en(load_en), .load_addr(load_addr), .load_data(load_data),
        .unload_en(unload_en), .unload_addr(unload_addr), .unload_data(unload_data)
    );

{dump_block}    initial clk = 0;
    always #5 clk = ~clk;

    initial begin
        #{watchdog_ns};
        $display("WATCHDOG TIMEOUT");
        $finish;
    end

    initial begin : STIM
        integer wait_cnt;
{vec_init}
        out_file = $fopen("{out_path}", "w");
        rst = 0; start = 0; load_en = 0; load_addr = 0; load_data = 0; unload_en = 0; unload_addr = 0; total_cycles = 0;
        repeat(8) @(posedge clk);
        rst = 1;
        repeat(4) @(posedge clk);

        $display("\\n================================================================");
        $display("  Pipelined Run Report  --  {design_name} (FFT-{n})");
        $display("================================================================");

        for (ti = 0; ti < {num_tests}; ti = ti + 1) begin
            @(posedge clk);
            load_cycles = 0; load_en = 1;
            for (i = 0; i < {n}; i = i + 1) begin
                load_addr = i[{addr_bits-1}:0]; load_data = tv[ti*{n} + i];
                @(posedge clk); load_cycles = load_cycles + 1;
            end
            load_en = 0;
            @(posedge clk); load_cycles = load_cycles + 1;

            cycle_count = 0; start = 1;
            @(posedge clk); start = 0;
            cycle_count = cycle_count + 1;

            wait_cnt = 0;
            while (!done && wait_cnt < {ready_timeout}) begin
                @(posedge clk); cycle_count = cycle_count + 1; wait_cnt = wait_cnt + 1;
            end
            @(posedge clk);

            unload_cycles_cnt = 0; unload_en = 1;
            for (i = 0; i < {n}; i = i + 1) begin
                unload_addr = i[{addr_bits-1}:0];
                @(posedge clk); unload_cycles_cnt = unload_cycles_cnt + 1;
                @(posedge clk); unload_cycles_cnt = unload_cycles_cnt + 1;
                @(posedge clk); unload_cycles_cnt = unload_cycles_cnt + 1;
                $fwrite(out_file, "%016h\\n", unload_data);
            end
            unload_en = 0;
            repeat(2) @(posedge clk);

            total_cycles = total_cycles + load_cycles + cycle_count + unload_cycles_cnt;
            $display("  -> Test %0d | Exec Cycles: %0d | Load: %0d | Unload: %0d", ti, cycle_count, load_cycles, unload_cycles_cnt);
        end
        $display("================================================================");
        $display("FINAL_METRICS | Total Cycles: %0d", total_cycles);
        $display("================================================================");

        $fclose(out_file);
        $finish;
    end
endmodule
"""
        os.makedirs(sim_dir, exist_ok=True)
        tb_file = os.path.join(sim_dir, f'tb_{design_name}.v')
        with open(tb_file, 'w') as f:
            f.write(tb)
        return tb_file

    @staticmethod
    def _sanitize_name(name):
        return re.sub(r'[^A-Za-z0-9_]', '_', name)

    # ------------------------------------------------------------------
    # File resolution for the generated FP32 cores
    # ------------------------------------------------------------------
    def core_file(self):
        return os.path.join(BASE_DIR, 'generated_cores', f'fp32_fft_{self.fft_size}',
                            f'fp32_fft_{self.fft_size}_core.v')

    @staticmethod
    def _top_file_for(verilog_file):
        # fp32_fft_<N>_core.v -> fp32_fft_<N>_top.v  (also accepts the mixed-style
        # <name>.v -> <name>_top.v naming used by performance_evaluator.py)
        if verilog_file.endswith('_core.v'):
            return verilog_file[:-len('_core.v')] + '_top.v'
        return verilog_file[:-2] + '_top.v'

    def run_verilog_simulation(self, verilog_file, design_name):
        design_name = self._sanitize_name(design_name)
        sim_dir = self.sim_dir
        os.makedirs(sim_dir, exist_ok=True)

        tb_file = self._generate_testbench(verilog_file, design_name)
        lib_sources = sorted(glob_module.glob(os.path.join(self.verilog_sources_dir, '*.v')))
        lib_sources += [os.path.join(self.shared_sources_dir, 'agu.v'),
                        os.path.join(self.shared_sources_dir, 'bit_reversal.v')]

        top_file = self._top_file_for(verilog_file)
        extra    = [top_file] if os.path.exists(top_file) else []
        vvp_path = os.path.join(sim_dir, f'{design_name}.vvp')

        compile_cmd = (
            ['iverilog', '-o', vvp_path, '-I', self.verilog_sources_dir, '-g2012',
             tb_file, verilog_file]
            + extra + lib_sources
        )

        try:
            res = subprocess.run(compile_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                print(f"[COMPILE ERROR] iverilog returned {res.returncode} for {design_name}")
                if res.stdout: print("[COMPILE STDOUT]\n" + res.stdout[:2000])
                if res.stderr: print("[COMPILE STDERR]\n" + res.stderr[:2000])
                return None
            sim_res = subprocess.run(['vvp', vvp_path], capture_output=True, text=True,
                                     timeout=self.sim_timeout, cwd=sim_dir)
            if sim_res.returncode != 0:
                print(f"[SIM ERROR] vvp returned {sim_res.returncode} for {design_name}")
                return None
            if "WATCHDOG TIMEOUT" in sim_res.stdout:
                print(f"[SIM ERROR] watchdog timeout for {design_name}")
                return None
            return os.path.join(sim_dir, f'{design_name}_output.txt'), sim_res.stdout
        except Exception as e:
            print(f"[SIM ERROR] {design_name}: {e}")
            return None

    def _parse_simulation_output(self, output_file):
        outputs = []
        try:
            with open(output_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    word = int(line, 16) & 0xFFFFFFFFFFFFFFFF
                    real = self.fp32_to_float(word >> 32)
                    imag = self.fp32_to_float(word & 0xFFFFFFFF)
                    outputs.append(real + 1j * imag)
        except Exception: return None
        # float64 container: the parsed values are exact FP32 numbers
        return np.array(outputs, dtype=np.complex128) if outputs else None

    def calculate_sqnr(self, golden, approximate):
        """Same SQNR as the mixed evaluator, with FP32 as the output format."""
        quantized_golden = np.array(
            [self.fp32_to_float(self.float_to_fp32(x.real)) +
             1j * self.fp32_to_float(self.float_to_fp32(x.imag)) for x in golden],
            dtype=np.complex128)

        noise_power = np.mean(np.abs(quantized_golden - approximate) ** 2)
        if noise_power == 0: return float('inf')
        signal_power = np.mean(np.abs(quantized_golden) ** 2)
        if signal_power == 0: return 0.0
        return float(10.0 * np.log10(signal_power / noise_power))

    def evaluate_design(self, verilog_file, design_name, chromosome=None):
        """Same interface as PerformanceEvaluator.evaluate_design.

        `chromosome` is accepted for drop-in compatibility and ignored: the FP32
        baseline has no per-stage precision choices.
        """
        design_name = self._sanitize_name(design_name)
        goldens = self.golden_outputs
        fail = {'sqnr': -100.0, 'avg_exec_cycles': -1, 'tot_sim_cycles': -1}

        run_result = self.run_verilog_simulation(verilog_file, design_name)
        if run_result is None: return fail
        output_file, sim_log = run_result

        avg_exec_cycles = "N/A"
        tot_sim_cycles = "N/A"
        execs = []
        for line in sim_log.splitlines():
            if "Exec Cycles:" in line:
                parts = line.split("|")
                for p in parts:
                    if "Exec Cycles:" in p:
                        execs.append(int(p.split(":")[1].strip()))
            if "FINAL_METRICS" in line:
                tot_sim_cycles = line.split("Total Cycles:")[1].strip()

        if execs:
            avg_exec_cycles = str(sum(execs) // len(execs))

        sim_outputs = self._parse_simulation_output(output_file)
        if sim_outputs is None or len(sim_outputs) == 0: return fail

        n, num_tests = self.fft_size, len(self.test_vectors)
        total_sqnr, valid = 0.0, 0
        finite_sum, finite_cnt, n_exact = 0.0, 0, 0
        per_signal = {}

        print("")
        print("-------------------------------------------------------")
        print(f"  Pipelined Metrics Breakdown - {design_name}")
        print(f"  > Avg FFT Execution Time : {avg_exec_cycles} clock cycles")
        print(f"  > Total Simulation Time  : {tot_sim_cycles} clock cycles")
        print(f"  > Golden reference       : FP32 input quantisation")
        print("-------------------------------------------------------")

        for i in range(min(num_tests, len(sim_outputs) // n)):
            approx = sim_outputs[i * n : (i + 1) * n]
            golden = goldens[i]
            label  = SIGNAL_LABELS[i]
            sqnr   = self.calculate_sqnr(golden, approx)
            per_signal[label] = sqnr

            if math.isinf(sqnr):
                print(f"  {label:<25}   inf dB (Exact)")
                total_sqnr += INF_SQNR_CREDIT; valid += 1
                n_exact += 1
            else:
                print(f"  {label:<25}  {sqnr:>10.2f} dB")
                total_sqnr += sqnr; valid += 1
                finite_sum += sqnr; finite_cnt += 1

        print("-------------------------------------------------------")
        avg_sqnr = total_sqnr / valid if valid > 0 else -100.0
        finite_avg = finite_sum / finite_cnt if finite_cnt > 0 else float('inf')
        print(f"  Avg SQNR (mixed-evaluator method, exact = {INF_SQNR_CREDIT:.0f} dB): {avg_sqnr:.2f} dB")
        print(f"  Avg SQNR over non-exact signals ({finite_cnt}/{valid})      : {finite_avg:.2f} dB")
        print("-------------------------------------------------------")

        try:
            avg_exec_int = int(avg_exec_cycles)
        except (ValueError, TypeError):
            avg_exec_int = -1
        try:
            tot_sim_int = int(tot_sim_cycles)
        except (ValueError, TypeError):
            tot_sim_int = -1

        return {
            'sqnr':             avg_sqnr,          # same definition as the mixed evaluator
            'avg_exec_cycles':  avg_exec_int,
            'tot_sim_cycles':   tot_sim_int,
            # reporting-only extras
            'sqnr_finite_avg':  finite_avg,
            'num_exact':        n_exact,
            'num_signals':      valid,
            'per_signal_sqnr':  per_signal,
        }

    def evaluate_size(self):
        """Evaluate the generated fp32_fft_<N> core for this evaluator's size."""
        core = self.core_file()
        if not os.path.isfile(core):
            raise FileNotFoundError(f"{core} missing - run fp32_template_generator.py first")
        return self.evaluate_design(core, f'fp32_fft_{self.fft_size}')


def _archive_perf_dir(perf_dir, keep_filename, archive_name="fp32_perf_artifacts.zip"):
    """Post-run cleanup of the perf/ scratch directory.

    - The `verilog_sources/` subdir only exists to give the twiddle ROM's
      relative-path parameter (`verilog_sources/twiddles_fp32_1024.txt`)
      somewhere to resolve to during simulation; it has no value once SQNR
      has been computed, so it is deleted outright rather than archived.
    - Everything else (compiled .vvp binaries, generated testbenches, raw
      unload dumps) is zipped into one archive so the SQNR summary
      (`keep_filename`) is the only loose file left in perf/.
    """
    scratch_rom_dir = os.path.join(perf_dir, "verilog_sources")
    if os.path.isdir(scratch_rom_dir):
        shutil.rmtree(scratch_rom_dir)

    to_archive = [
        f for f in sorted(os.listdir(perf_dir))
        if f not in (keep_filename, archive_name)
        and os.path.isfile(os.path.join(perf_dir, f))
    ]
    if not to_archive:
        return

    archive_path = os.path.join(perf_dir, archive_name)
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in to_archive:
            zf.write(os.path.join(perf_dir, fname), arcname=fname)

    for fname in to_archive:
        os.remove(os.path.join(perf_dir, fname))


def main():
    ap = argparse.ArgumentParser(description="FP32 baseline SQNR (mixed-evaluator methodology)")
    ap.add_argument("--sizes", type=int, nargs="*", default=ALL_SIZES)
    ap.add_argument("--shared-dir", default=DEFAULT_SHARED_DIR,
                    help="directory with agu.v and bit_reversal.v")
    ap.add_argument("--out", default=os.path.join(SIM_DIR_ROOT, 'perf', 'fp32_sqnr_results.txt'),
                    help="summary table (text)")
    args = ap.parse_args()

    for f in ("agu.v", "bit_reversal.v"):
        if not os.path.isfile(os.path.join(args.shared_dir, f)):
            raise SystemExit(f"{f} not found in {args.shared_dir} (use --shared-dir)")

    rows = []
    for n in args.sizes:
        ev = FP32PerformanceEvaluator(n, shared_sources_dir=args.shared_dir)
        r = ev.evaluate_size()
        rows.append((n, r))

    hdr = (f"{'N':>6} | {'Exec cycles':>11} | {'Avg SQNR (dB)':>13} | "
           f"{'Avg non-exact (dB)':>18} | {'Exact':>7}")
    sep = "-" * len(hdr)
    lines = ["FP32 baseline - SQNR using the mixed-precision evaluator methodology",
             f"(11 signals; exact match counted as {INF_SQNR_CREDIT:.0f} dB in 'Avg SQNR')",
             "", hdr, sep]
    for n, r in rows:
        if r['avg_exec_cycles'] < 0:
            lines.append(f"{n:>6} | {'FAILED':>11} | {'-':>13} | {'-':>18} | {'-':>7}")
            continue
        fin = r['sqnr_finite_avg']
        fin_s = "all exact" if math.isinf(fin) else f"{fin:.2f}"
        lines.append(f"{n:>6} | {r['avg_exec_cycles']:>11} | {r['sqnr']:>13.2f} | "
                     f"{fin_s:>18} | {r['num_exact']:>3}/{r['num_signals']:<3}")
    text = "\n".join(lines) + "\n"
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        f.write(text)
    print("\n" + text + f"table: {args.out}")

    perf_dir = os.path.dirname(os.path.abspath(args.out))
    _archive_perf_dir(perf_dir, keep_filename=os.path.basename(args.out))


if __name__ == "__main__":
    main()
