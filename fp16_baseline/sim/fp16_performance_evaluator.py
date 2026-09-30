"""
Performance Evaluation Module -- FP16 Baseline Edition
======================================================
FP16 (IEEE 754 binary16) counterpart of fp32_baseline/sim/
fp32_performance_evaluator.py, itself the counterpart of
performance_evaluator.py, so all three precisions are scored with EXACTLY the
same methodology as the mixed-precision FP4/FP8 cores:

  * same 11 test signals (SIGNAL_LABELS / _generate_test_vectors unchanged)
  * same golden: FFT of the input quantised to the design's input format
  * same SQNR: golden re-quantised to the design's output format, then
        10*log10( mean|golden_q|^2 / mean|golden_q - rtl|^2 )
  * same averaging over signals, with an exact match counted as 100 dB
  * same testbench timing (load, start, wait for done, 3-cycle unload)

What differs is only what has to differ for an FP16 datapath:
  * input / output / twiddle format is FP16 (no FP8/FP4 conversions, no
    chromosome-driven precision selection)
  * 32-bit load / unload words ({real, imag}), 32-bit twiddle ROM
  * the golden FFT is computed in float64 before quantisation
  * sources: fp16_baseline/source/*.v plus ONLY agu.v and bit_reversal.v from
    the shared ../verilog_sources
  * --memory selects the memory implementation: `regarray` (behavioural
    register arrays, the FPGA track) or `sram` (SRAM macros, the ASIC track);
    with `sram`, --sram-width picks the macro model. Simulation results are
    identical for every combination -- the flags exist so the same source list
    can be handed to whichever synthesis flow is being run.

Location: fp16_baseline/sim/fp16_performance_evaluator.py
All paths are resolved relative to this file, so it can be run from anywhere:

    python3 fp16_baseline/sim/fp16_performance_evaluator.py            # all sizes
    python3 fp16_baseline/sim/fp16_performance_evaluator.py --sizes 256 1024

or used from Python with the same interface as the mixed/FP32 evaluators:

    ev = FP16PerformanceEvaluator(256)
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

SIM_DIR_ROOT = os.path.dirname(os.path.abspath(__file__))       # fp16_baseline/sim
BASE_DIR = os.path.dirname(SIM_DIR_ROOT)                        # fp16_baseline
REPO_ROOT = os.path.dirname(BASE_DIR)                           # repo root
DEFAULT_SHARED_DIR = os.path.join(REPO_ROOT, "verilog_sources")

# The 64-bit macro model lives in the FP32 baseline; it is referenced rather
# than copied so the repository has exactly one definition of it.
SRAM64_MODEL = os.path.join(REPO_ROOT, "fp32_baseline", "source", "mem_sram",
                            "sram_512x64_2rw.v")
MEM_REGARRAY_DIR = "mem_regarray"
MEM_SRAM_DIR = "mem_sram"
SRAM32_NATIVE = "sram_512x32_2rw.v"
SRAM32_FROM64 = os.path.join("sram_variants", "sram_512x32_2rw_from64.v")

FP16_MAX = float(np.finfo(np.float16).max)     # 65504.0
INF_SQNR_CREDIT = 100.0          # same credit the mixed evaluator gives an exact match
ALL_SIZES = [2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]


def collect_source_files(source_dir, memory="regarray", sram_width=32):
    """The source list for both simulation and synthesis.

    `source_dir/*.v` holds the format RTL (arithmetic, butterfly, twiddle ROM).
    The memory lives in one of two sibling directories, and exactly one is
    compiled -- both define `fp16_dual_bank_memory_concurrent` with the same
    ports, so including both would be a duplicate module definition:

      memory == "regarray"  -> mem_regarray/fp16_memory.v
            Behavioural register arrays; Vivado infers BRAM. The FPGA track.
            Self-contained: no macro model, no Liberty.

      memory == "sram"      -> mem_sram/fp16_memory.v + one macro model
            SRAM-macro based. The ASIC track. `sram_width` then picks the macro
            model: 32 = native width-matched sram_512x32_2rw (needs a
            compiler-generated Liberty), 64 = wrapper over the existing
            512x64 macro, which also pulls in the FP32 baseline's macro model.

    Simulation results are identical for every combination; the choice only
    matters downstream, in synthesis.
    """
    source_dir = os.path.abspath(source_dir)
    files = sorted(glob_module.glob(os.path.join(source_dir, "*.v")))

    # Memory RTL must come from mem_regarray/ or mem_sram/, never from the
    # source/ root. A stale root-level copy (left behind by the pre-two-track
    # layout) would otherwise be globbed in alongside the chosen variant and
    # produce a duplicate definition of fp16_dual_bank_memory_concurrent.
    STALE_AT_ROOT = ("fp16_memory.v", "sram_512x32_2rw.v")
    stale = [f for f in files if os.path.basename(f) in STALE_AT_ROOT]
    if stale:
        for f in stale:
            print(f"[fp16] WARNING ignoring stale {os.path.relpath(f, source_dir)} "
                  f"in source/ root -- memory now lives in mem_regarray/ or "
                  f"mem_sram/. Delete it.")
        files = [f for f in files if f not in stale]

    if memory == "regarray":
        files.append(os.path.join(source_dir, MEM_REGARRAY_DIR, "fp16_memory.v"))
        return files

    if memory != "sram":
        raise ValueError(f"memory must be 'regarray' or 'sram', got {memory!r}")

    mem_dir = os.path.join(source_dir, MEM_SRAM_DIR)
    files.append(os.path.join(mem_dir, "fp16_memory.v"))
    if sram_width == 64:
        files.append(os.path.join(mem_dir, SRAM32_FROM64))
        files.append(SRAM64_MODEL)
    else:
        files.append(os.path.join(mem_dir, SRAM32_NATIVE))
    return files


class FP16PerformanceEvaluator:
    def __init__(self, fft_size, shared_sources_dir=DEFAULT_SHARED_DIR,
                 sim_dir=None, sim_timeout=600, dump_vcd=False,
                 memory="regarray", sram_width=32):
        self.fft_size            = fft_size
        self.num_stages          = int(math.log2(fft_size))
        self.verilog_sources_dir = os.path.join(BASE_DIR, 'source')
        self.shared_sources_dir  = os.path.abspath(shared_sources_dir)
        self.sim_dir             = os.path.abspath(sim_dir or os.path.join(SIM_DIR_ROOT, 'perf'))
        self.sim_timeout         = sim_timeout
        self.memory              = memory
        self.sram_width          = sram_width
        # Off by default: this does not change anything about the SQNR/cycle
        # measurement this class exists for. When True, _generate_testbench
        # also dumps a VCD of the RTL simulation (same stimulus, same DUT),
        # for run_fp16_synthesis.py to feed into OpenSTA's `read_vcd` as a
        # real-activity replacement for the flat 0.2 toggle-rate guess.
        self.dump_vcd            = dump_vcd
        self.test_vectors        = self._generate_test_vectors()
        self.golden_outputs      = self._compute_golden_outputs()

    # ------------------------------------------------------------------
    # Test vectors -- IDENTICAL to performance_evaluator.py and the FP32 one
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
    # Golden: FFT of the FP16-quantised input (computed in float64)
    # ------------------------------------------------------------------
    def _compute_golden_outputs(self):
        goldens = []
        for v in self.test_vectors:
            v_quantized = np.array(
                [self.fp16_to_float(self.float_to_fp16(x.real)) +
                 1j * self.fp16_to_float(self.float_to_fp16(x.imag)) for x in v],
                dtype=np.complex128)
            goldens.append(np.fft.fft(v_quantized))
        return goldens

    # ==================================================================
    # Float <-> FP16 helpers (round-to-nearest-even, saturating, like the RTL)
    # ==================================================================
    @staticmethod
    def float_to_fp16(val):
        val = float(val)
        if val == 0.0:
            return 0
        if abs(val) > FP16_MAX:
            val = math.copysign(FP16_MAX, val)
        bits = struct.unpack('>H', struct.pack('>e', val))[0]
        # round-to-nearest may still produce Inf just above FP16_MAX: saturate
        if (bits & 0x7FFF) == 0x7C00:
            bits = (bits & 0x8000) | 0x7BFF
        return 0 if bits == 0x8000 else bits      # the design emits +0

    @staticmethod
    def fp16_to_float(bits):
        return struct.unpack('>e', struct.pack('>H', bits & 0xFFFF))[0]

    # ------------------------------------------------------------------
    # Testbench -- same timing as the mixed/FP32 evaluators, 32-bit words
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
                word = (self.float_to_fp16(sample.real) << 16) | self.float_to_fp16(sample.imag)
                vec_hex_lines.append(f"        tv[{ti*n + si}] = 32'h{word:08x};")
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
    reg load_en; reg [{addr_bits-1}:0] load_addr; reg [31:0] load_data;
    reg unload_en; reg [{addr_bits-1}:0] unload_addr; wire [31:0] unload_data;
    integer i, ti, out_file;
    integer cycle_count, total_cycles, load_cycles, unload_cycles_cnt;
    reg [31:0] tv [{num_tests*n - 1}:0];

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
                $fwrite(out_file, "%08h\\n", unload_data);
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
    # File resolution for the generated FP16 cores
    # ------------------------------------------------------------------
    def core_file(self):
        return os.path.join(BASE_DIR, 'generated_cores', f'fp16_fft_{self.fft_size}',
                            f'fp16_fft_{self.fft_size}_core.v')

    @staticmethod
    def _top_file_for(verilog_file):
        # fp16_fft_<N>_core.v -> fp16_fft_<N>_top.v  (also accepts the mixed-style
        # <name>.v -> <name>_top.v naming used by performance_evaluator.py)
        if verilog_file.endswith('_core.v'):
            return verilog_file[:-len('_core.v')] + '_top.v'
        return verilog_file[:-2] + '_top.v'

    def run_verilog_simulation(self, verilog_file, design_name):
        design_name = self._sanitize_name(design_name)
        sim_dir = self.sim_dir
        os.makedirs(sim_dir, exist_ok=True)

        tb_file = self._generate_testbench(verilog_file, design_name)
        lib_sources = collect_source_files(self.verilog_sources_dir,
                                          self.memory, self.sram_width)
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
                    word = int(line, 16) & 0xFFFFFFFF
                    real = self.fp16_to_float(word >> 16)
                    imag = self.fp16_to_float(word & 0xFFFF)
                    outputs.append(real + 1j * imag)
        except Exception: return None
        # float64 container: the parsed values are exact FP16 numbers
        return np.array(outputs, dtype=np.complex128) if outputs else None

    def calculate_sqnr(self, golden, approximate):
        """Same SQNR as the mixed evaluator, with FP16 as the output format."""
        quantized_golden = np.array(
            [self.fp16_to_float(self.float_to_fp16(x.real)) +
             1j * self.fp16_to_float(self.float_to_fp16(x.imag)) for x in golden],
            dtype=np.complex128)

        noise_power = np.mean(np.abs(quantized_golden - approximate) ** 2)
        if noise_power == 0: return float('inf')
        signal_power = np.mean(np.abs(quantized_golden) ** 2)
        if signal_power == 0: return 0.0
        return float(10.0 * np.log10(signal_power / noise_power))

    def evaluate_design(self, verilog_file, design_name, chromosome=None):
        """Same interface as PerformanceEvaluator.evaluate_design.

        `chromosome` is accepted for drop-in compatibility and ignored: the FP16
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
        execs, loads, unloads = [], [], []
        for line in sim_log.splitlines():
            if "Exec Cycles:" in line:
                for p in line.split("|"):
                    if "Exec Cycles:" in p:
                        execs.append(int(p.split(":")[1].strip()))
                    elif "Load:" in p:
                        loads.append(int(p.split(":")[1].strip()))
                    elif "Unload:" in p:
                        unloads.append(int(p.split(":")[1].strip()))
            if "FINAL_METRICS" in line:
                tot_sim_cycles = line.split("Total Cycles:")[1].strip()

        if execs:
            avg_exec_cycles = str(sum(execs) // len(execs))
        avg_load_cycles   = (sum(loads) // len(loads)) if loads else -1
        avg_unload_cycles = (sum(unloads) // len(unloads)) if unloads else -1

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
        print(f"  > Golden reference       : FP16 input quantisation")
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

        # Both cycle denominators the throughput/energy table needs:
        #   compute-only  = avg_exec_cycles          (start-to-done, canonical)
        #   end-to-end    = load + exec + unload     (non-overlapped batch)
        e2e = (avg_load_cycles + avg_exec_int + avg_unload_cycles
               if (avg_load_cycles >= 0 and avg_unload_cycles >= 0 and avg_exec_int > 0)
               else -1)

        return {
            'sqnr':             avg_sqnr,          # same definition as the mixed evaluator
            'avg_exec_cycles':  avg_exec_int,
            'avg_load_cycles':  avg_load_cycles,
            'avg_unload_cycles': avg_unload_cycles,
            'avg_e2e_cycles':   e2e,
            'tot_sim_cycles':   tot_sim_int,
            # reporting-only extras
            'sqnr_finite_avg':  finite_avg,
            'num_exact':        n_exact,
            'num_signals':      valid,
            'per_signal_sqnr':  per_signal,
        }

    def evaluate_size(self):
        """Evaluate the generated fp16_fft_<N> core for this evaluator's size."""
        core = self.core_file()
        if not os.path.isfile(core):
            raise FileNotFoundError(f"{core} missing - run fp16_template_generator.py first")
        return self.evaluate_design(core, f'fp16_fft_{self.fft_size}')


def _archive_perf_dir(perf_dir, keep_filename, archive_name="fp16_perf_artifacts.zip"):
    """Post-run cleanup of the perf/ scratch directory: everything except the
    SQNR summary (compiled .vvp binaries, generated testbenches, raw unload
    dumps, VCDs) is zipped into one archive so the summary is the only loose
    file left in perf/."""
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
    ap = argparse.ArgumentParser(description="FP16 baseline SQNR (mixed-evaluator methodology)")
    ap.add_argument("--sizes", type=int, nargs="*", default=ALL_SIZES)
    ap.add_argument("--shared-dir", default=DEFAULT_SHARED_DIR,
                    help="directory with agu.v and bit_reversal.v")
    ap.add_argument("--memory", choices=("regarray", "sram"), default="regarray",
                    help="memory implementation: regarray = behavioural "
                         "register arrays, the FPGA track (default, "
                         "self-contained); sram = SRAM macros, the ASIC track. "
                         "Simulation results are identical either way.")
    ap.add_argument("--sram-width", type=int, choices=(32, 64), default=32,
                    help="only with --memory sram: which sram_512x32_2rw model "
                         "to compile. 32 = native width-matched (needs a "
                         "compiler-generated Liberty), 64 = wrapper over the "
                         "existing 512x64 macro.")
    ap.add_argument("--out", default=os.path.join(SIM_DIR_ROOT, 'perf', 'fp16_sqnr_results.txt'),
                    help="summary table (text)")
    args = ap.parse_args()

    for f in ("agu.v", "bit_reversal.v"):
        if not os.path.isfile(os.path.join(args.shared_dir, f)):
            raise SystemExit(f"{f} not found in {args.shared_dir} (use --shared-dir)")

    rows = []
    for n in args.sizes:
        ev = FP16PerformanceEvaluator(n, shared_sources_dir=args.shared_dir,
                                     memory=args.memory,
                                     sram_width=args.sram_width)
        r = ev.evaluate_size()
        rows.append((n, r))

    nsig = rows[0][1]['num_signals'] if rows else 0
    COLS = [
        ("FFT size", "points"),
        ("Compute cycles", "clock cycles"),
        ("Input load", "clock cycles"),
        ("Output unload", "clock cycles"),
        ("End-to-end cycles", "clock cycles"),
        ("Average SQNR", "dB"),
        ("Average SQNR, non-exact only", "dB"),
        ("Bit-exact signals", "count of %d" % nsig),
    ]

    body = []
    for n, r in rows:
        if r['avg_exec_cycles'] < 0:
            body.append([str(n), "FAILED", "-", "-", "-", "-", "-", "-"])
            continue
        fin = r['sqnr_finite_avg']
        body.append([
            str(n),
            str(r['avg_exec_cycles']),
            str(r['avg_load_cycles']),
            str(r['avg_unload_cycles']),
            str(r['avg_e2e_cycles']),
            "%.2f" % r['sqnr'],
            "all exact" if math.isinf(fin) else "%.2f" % fin,
            "%d/%d" % (r['num_exact'], r['num_signals']),
        ])

    widths = [max(len(COLS[i][0]), len(COLS[i][1]),
                  max((len(b[i]) for b in body), default=0))
              for i in range(len(COLS))]
    hdr = " | ".join(c[0].rjust(w) for c, w in zip(COLS, widths))
    unit = " | ".join(c[1].rjust(w) for c, w in zip(COLS, widths))
    sep = "-" * len(hdr)

    lines = ["FP16 BASELINE - ACCURACY AND CYCLE COUNTS",
             "",
             "Signal-to-quantisation-noise ratio measured with the same methodology as the",
             "mixed-precision evaluator: %d test signals, and a bit-exact result" % nsig,
             "credited as %.0f dB in the 'Average SQNR' column." % INF_SQNR_CREDIT,
             "",
             "Memory implementation: %s%s" % (
                 args.memory,
                 " (--sram-width %s)" % args.sram_width if args.memory == "sram" else ""),
             "",
             "Two cycle counts are reported, and both are carried through to throughput",
             "and energy as equal columns:",
             "  Compute cycles    start of transform to done. Canonical in this flow, and",
             "                    the fair basis for comparing precisions, since load and",
             "                    unload cost the same at every precision.",
             "  End-to-end cycles input load + compute + output unload. This architecture",
             "                    does not overlap I/O with compute: the two memory banks",
             "                    ping-pong between FFT STAGES, so double-buffering the",
             "                    input against compute would need a third bank.",
             "", hdr, unit, sep]
    lines.extend(" | ".join(v.rjust(w) for v, w in zip(b, widths)) for b in body)
    text = "\n".join(lines) + "\n"
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        f.write(text)
    print("\n" + text + f"table: {args.out}")

    perf_dir = os.path.dirname(os.path.abspath(args.out))
    _archive_perf_dir(perf_dir, keep_filename=os.path.basename(args.out))


if __name__ == "__main__":
    main()
