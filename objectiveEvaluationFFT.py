"""
Objective Evaluation for Mixed-Precision FFT Optimization
Uses Vivado synthesis critical path delay as 4th objective (on-chip latency).
Incorporates quadratic SQNR scaling to heavily penalize severe quantization noise.
"""

import numpy as np
import subprocess
import os
import csv
import hashlib
import math
import re
from pymoo.core.problem import Problem
from concurrent.futures import ThreadPoolExecutor, as_completed

from globalVariablesMixedFFT import *
from fft_template_generator import FFTTemplateGenerator
from performance_evaluator import PerformanceEvaluator
from energyObjective import (energy_objectives, penalty_objectives,
                             ENERGY_OBJECTIVES)


class MixedPrecisionFFTProblem(Problem):
    def __init__(self, fft_size=8, **kwargs):
        if OBJECTIVES != ENERGY_OBJECTIVES:
            raise ValueError(
                f"OBJECTIVES={OBJECTIVES} in globalVariablesMixedFFT.py but "
                f"energyObjective supplies {ENERGY_OBJECTIVES}. These must "
                f"agree or pymoo will silently mis-shape the objective array.")
        self.fft_size     = fft_size
        self.template_gen = FFTTemplateGenerator(fft_size)
        self.perf_eval    = PerformanceEvaluator(fft_size)

        chrom_length = self.template_gen.get_chromosome_length()

        super().__init__(
            n_var=chrom_length,
            n_obj=OBJECTIVES,           # energy, sqnr_error^2, latency
            n_ieq_constr=3,
            xl=[0] * chrom_length,
            xu=[1] * chrom_length,
            vtype=int,
            elementwise_evaluation=False,
            **kwargs
        )

        log_message(f"Initialized FFT-{fft_size} problem: 3 objectives "
                    f"(energy/transform, SQNR error^2, latency); area is a constraint")

    def _evaluate(self, X, out, *args, **kwargs):
        global CURRENT_GEN
        log_message(f"=== Generation {CURRENT_GEN} ===", level='GEN')
        with open('generation.txt', 'w') as f:
            f.write(str(CURRENT_GEN))
        CURRENT_GEN += 1

        F = [None] * len(X)
        G = [None] * len(X)

        with ThreadPoolExecutor(max_workers=SOLUTION_THREADS) as executor:
            futures = {executor.submit(self.evaluate_solution, X[i], i): i for i in range(len(X))}
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    f_vals, g_vals = future.result()
                    F[idx] = f_vals
                    G[idx] = g_vals
                except Exception as e:
                    log_message(f"Solution {idx} failed: {e}", level='ERROR')
                    pf, pg = penalty_objectives()
                    F[idx] = pf
                    G[idx] = pg

        out["F"] = np.array(F)
        out["G"] = np.array(G)
        log_message(f"Generation {CURRENT_GEN-1} complete")

    def evaluate_solution(self, chromosome, sol_id):
        log_message(f"Evaluating solution {sol_id}: {[int(x) for x in chromosome]}")

        chrom_hash = self._hash_chromosome(chromosome)
        if ENABLE_RESULT_CACHE and chrom_hash in RESULT_CACHE:
            return self._compute_objectives_and_constraints(RESULT_CACHE[chrom_hash])

        design_name = f"fft_{self.fft_size}_sol{sol_id}_gen{CURRENT_GEN}"

        core_file = os.path.join(GENERATED_DESIGNS_DIR, f"{design_name}.v")
        core_file, top_file = self.template_gen.generate_verilog(chromosome, core_file)

        m = self._run_vivado_synthesis(design_name, core_file, top_file)
        perf = self._run_performance_evaluation(core_file, design_name, chromosome)
        sqnr            = perf['sqnr']
        avg_exec_cycles = perf['avg_exec_cycles']
        tot_sim_cycles  = perf['tot_sim_cycles']

        crit_delay   = m['critical_path_delay_ns']
        norm_latency = self._compute_actual_normalized_latency(crit_delay)

        results = {
            'power':            m['total_power_w'],
            'dyn_power':        m['dynamic_power_w'],
            'static_power_w':   m['static_power_w'],
            'dyn_vectorless_w': m['dynamic_power_vectorless_w'],
            'saif_used':        m['saif_used'],
            'checksum_match':   m['checksum_match'],
            'area':             m['lut_count'],
            'sqnr':             sqnr,
            'norm_latency':     norm_latency,
            'crit_delay_ns':    crit_delay,
            'avg_exec_cycles':  avg_exec_cycles,
            'tot_sim_cycles':   tot_sim_cycles,
            'dsp':              m['dsp_count'],
            'bram':             m['bram_count'],
            'ff':               m['ff_count'],
            'lutram':           m['lutram_count'],
            'fmax_mhz':         m['fmax_mhz'],
            'dsp48_primitives': m['dsp48_primitives'],
            'kept_fp8_mul':     m['kept_fp8_mul'],
            'kept_fp4_mul':     m['kept_fp4_mul'],
        }

        RESULT_CACHE[chrom_hash] = results
        objs, cons = self._compute_objectives_and_constraints(results)
        self._save_solution_result(sol_id, chromosome, results)

        # Two ways this design's energy number can be worthless. Both are loud:
        # a silent inert objective is the exact failure this rewrite exists to
        # remove, so it must never pass unremarked.
        if not results['saif_used']:
            log_message(
                f"Solution {sol_id} ({design_name}): SAIF NOT APPLIED - dynamic "
                f"power is a vectorless guess and will NOT vary with the "
                f"chromosome. Inspect reports/{design_name}_saif.log.",
                level='ERROR')
        if not results['checksum_match']:
            log_message(
                f"Solution {sol_id} ({design_name}): SYNTH CHECKSUM MISMATCH - the "
                f"SAIF describes a different netlist than the one annotated. "
                f"Energy for this design is not trustworthy.", level='ERROR')

        log_message(
            f"Solution {sol_id}: E={results.get('energy_pj', -1.0):.1f} pJ/xfrm "
            f"(dyn={results.get('dyn_power_w_used', 0.0):.4f}W "
            f"saif={results['saif_used']} chksum={results['checksum_match']}), "
            f"A={results['area']} LUTs, LUTRAM={results['lutram']}, "
            f"DSP={results['dsp']}, BRAM={results['bram']}, FF={results['ff']}, "
            f"SQNR={sqnr:.2f}dB, CritDelay={crit_delay:.3f}ns -> "
            f"NormLat={norm_latency:.3f}x, ExecCycles={avg_exec_cycles}, "
            f"TotSimCycles={tot_sim_cycles}"
        )

        return objs, cons

    def _hash_chromosome(self, chromosome):
        return hashlib.md5(''.join(map(str, chromosome)).encode()).hexdigest()

    _CHECKSUM_RE = re.compile(r"Synth Design complete \| Checksum:\s*([0-9a-fA-F]+)")

    def _run_vivado_synthesis(self, design_name, core_file, top_file):
        """
        Two Vivado passes per design.

          1. generate_saif_funcsim.tcl - synthesises, writes a post-synthesis
             funcsim netlist, simulates it against the activity testbench, and
             logs a SAIF whose net names match that netlist.
          2. vivado_synthesis_v2.tcl   - synthesises identically, annotates that
             SAIF, and reports utilisation / timing / split power.

        Both passes print "Synth Design complete | Checksum: xxxxxxxx". Those
        MUST be equal, or the SAIF describes a different netlist than the one
        being annotated. Vivado is deterministic for identical inputs, but this
        compares rather than assuming, and marks the design untrusted if they
        differ.

        Returns the metrics dict from _parse_vivado_metrics, plus
        'checksum_match'.
        """
        log_message(f"Running Vivado SAIF + power passes for {design_name}")

        csv_output  = os.path.join(REPORTS_DIR, f"{design_name}_metrics.csv")
        verilog_dir = os.path.abspath(VERILOG_SOURCES_DIR)
        core_abs    = os.path.abspath(core_file)
        top_abs     = os.path.abspath(top_file)
        tb_abs      = os.path.abspath(POWER_TB_FILE)
        saif_path   = os.path.abspath(os.path.join(SIMULATION_DIR, f"{design_name}.saif"))
        saif_log    = os.path.abspath(os.path.join(REPORTS_DIR, f"{design_name}_saif.log"))
        pwr_log     = os.path.abspath(os.path.join(REPORTS_DIR, f"{design_name}_pwr.log"))

        if not os.path.exists(tb_abs):
            log_message(
                f"Activity testbench {tb_abs} is MISSING. Without it there is no "
                f"SAIF, report_power falls back to a vectorless guess identical "
                f"for every chromosome, and the energy objective is inert. "
                f"Refusing to evaluate {design_name}.", level='ERROR')
            return self._failed_metrics('no_activity_testbench')

        saif_cmd = [
            VIVADO_PATH, '-mode', 'batch', '-source', './generate_saif_funcsim.tcl',
            '-nojournal', '-log', saif_log, '-tclargs',
            design_name, core_abs, top_abs, verilog_dir, tb_abs, saif_path,
            str(self.fft_size), str(SAIF_FRAMES), FPGA_DEVICE, str(CLOCK_PERIOD),
        ]
        pwr_cmd = [
            VIVADO_PATH, '-mode', 'batch', '-source', './vivado_synthesis_v2.tcl',
            '-nojournal', '-log', pwr_log, '-tclargs',
            design_name, csv_output, str(CLOCK_PERIOD),
            core_abs, top_abs, verilog_dir, FPGA_DEVICE, saif_path,
            str(USE_DSP), SAIF_STRIP_PATH,
        ]

        for label, cmd in (("saif", saif_cmd), ("power", pwr_cmd)):
            try:
                result = subprocess.run(cmd, capture_output=True, text=True,
                                        timeout=VIVADO_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                log_message(f"{label} pass TIMEOUT ({VIVADO_TIMEOUT_S}s) for "
                            f"{design_name}", level='ERROR')
                return self._failed_metrics(f"{label}_timeout")
            except Exception as e:
                log_message(f"{label} pass invocation error for {design_name}: {e}",
                            level='ERROR')
                return self._failed_metrics(f"{label}_invoke_error")

            if result.returncode != 0:
                log_message(f"{label} pass failed for {design_name} "
                            f"(exit code {result.returncode})", level='ERROR')
                self._extract_and_log_vivado_errors(design_name)
                return self._failed_metrics(f"{label}_nonzero_exit")

        metrics = self._parse_vivado_metrics(csv_output)
        metrics["checksum_match"] = self._checksums_match(saif_log, pwr_log,
                                                          design_name)
        # Both passes are done with the scratch now. The SAIF under ./sim stays.
        if CLEAN_SAIF_WORKDIRS:
            self._reclaim_saif_workdir(design_name)
        return metrics

    @staticmethod
    def _reclaim_saif_workdir(design_name):
        """Delete /tmp/fsaif_<design>/ once both passes have read it.

        Best effort: a sweep must never die because scratch could not be
        removed, so every failure here is logged and swallowed."""
        import shutil
        work = os.path.join("/tmp", f"fsaif_{design_name}")
        if not os.path.isdir(work):
            return
        try:
            shutil.rmtree(work)
        except OSError as e:
            log_message(f"could not reclaim {work}: {e}", level='WARN')

    def _checksums_match(self, saif_log, pwr_log, design_name):
        def grab(path):
            try:
                with open(path, "r", errors="replace") as f:
                    for line in f:
                        m = self._CHECKSUM_RE.search(line)
                        if m:
                            return m.group(1).lower()
            except OSError:
                return None
            return None

        a, b = grab(saif_log), grab(pwr_log)
        if a is None or b is None:
            log_message(f"{design_name}: no synth checksum in one or both logs "
                        f"(saif={a}, power={b}); cannot prove the SAIF and the "
                        f"annotated netlist are the same design", level='WARN')
            return 0
        if a != b:
            log_message(f"{design_name}: SYNTH CHECKSUM MISMATCH saif={a} "
                        f"power={b}", level='ERROR')
            return 0
        return 1

    def _failed_metrics(self, reason=""):
        """Metrics for a design whose toolflow failed. Dominated, never rewarded."""
        return {
            "total_power_w":               MAX_POWER_W * 2,
            "dynamic_power_w":             None,
            "static_power_w":              None,
            "dynamic_power_vectorless_w":  None,
            "saif_used":                   0,
            "saif_read":                   0,
            "lut_count":                   MAX_AREA_LUTS * 2,
            "lutram_count":                0,
            "critical_path_delay_ns":      200.0,
            "dsp_count":                   0,
            "bram_count":                  0,
            "ff_count":                    0,
            "fmax_mhz":                    0.0,
            "dsp48_primitives":            0,
            "opt_design_ran":              0,
            "kept_fp8_mul":                0,
            "kept_fp4_mul":                0,
            "checksum_match":              0,
            "failure_reason":              reason,
        }

    def _extract_and_log_vivado_errors(self, design_name):
        log_candidates = ['vivado.log', f'./vivado_projects/{design_name}/vivado.log']
        log_parsed = False

        for log_path in log_candidates:
            if os.path.exists(log_path):
                log_parsed = True
                log_message(f"Parsing tool log [{log_path}] for fatal signatures...", level='WARN')
                
                error_lines = []
                with open(log_path, 'r') as f:
                    for line in f:
                        if "ERROR:" in line or "CRITICAL WARNING:" in line or "FATAL:" in line:
                            error_lines.append(line.strip())
                
                if error_lines:
                    log_message(f"=== FOUND {len(error_lines)} VIVADO FAULT SIGNATURES ===", level='ERROR')
                    for err in error_lines[-10:]: 
                        log_message(f"  >>> {err}", level='ERROR')
                    log_message("=================================================", level='ERROR')
                else:
                    log_message(f"No explicit ERROR strings found inside {log_path}. Check for silent system process terminations (OOM).", level='WARN')
                break
                
        if not log_parsed:
            log_message("Could not locate a raw vivado.log file in standard workspace paths. Vivado likely failed before initializing logging.", level='ERROR')

    # Metric name -> caster. Ints are cast via float first because the TCL
    # writes some of them from a [0-9.]+ regex ("4.0" for bram_count).
    _METRIC_CASTS = {
        "total_power_w": float, "dynamic_power_w": float,
        "static_power_w": float, "dynamic_power_vectorless_w": float,
        "critical_path_delay_ns": float, "fmax_mhz": float,
        "lut_count": int, "lutram_count": int, "dsp_count": int,
        "bram_count": int, "ff_count": int, "saif_used": int,
        "saif_read": int, "dsp48_primitives": int, "opt_design_ran": int,
        "kept_fp8_mul": int, "kept_fp4_mul": int,
    }

    def _parse_vivado_metrics(self, csv_file):
        """Read the v2 TCL's Metric,Value CSV into a dict, keeping the failure
        defaults for anything absent so a truncated CSV cannot masquerade as a
        cheap design."""
        metrics = self._failed_metrics("csv_unparsed")
        try:
            with open(csv_file, "r") as f:
                for row in csv.DictReader(f):
                    key = (row.get("Metric") or "").strip()
                    raw = (row.get("Value") or "").strip()
                    cast = self._METRIC_CASTS.get(key)
                    if cast is None or raw == "":
                        continue
                    try:
                        metrics[key] = int(float(raw)) if cast is int else float(raw)
                    except (TypeError, ValueError):
                        log_message(f"{csv_file}: could not cast {key}={raw!r}",
                                    level='WARN')
            metrics.pop("failure_reason", None)
        except Exception as e:
            log_message(f"Error parsing metrics from {csv_file}: {e}", level='ERROR')
        return metrics

    def _compute_actual_normalized_latency(self, crit_delay_ns):
        if crit_delay_ns <= 0 or math.isnan(crit_delay_ns) or math.isinf(crit_delay_ns):
            return 10.0

        norm = crit_delay_ns / REFERENCE_CLOCK_PERIOD_NS
        num_stages = self.template_gen.num_stages
        pipeline_factor = max(1.0, num_stages / 6.0)

        return min(norm * pipeline_factor, 10.0)

    def _run_performance_evaluation(self, verilog_file, design_name, chromosome=None):
        try:
            return self.perf_eval.evaluate_design(verilog_file, design_name, chromosome=chromosome)
        except Exception as e:
            log_message(f"Performance evaluation failed: {e}", level='ERROR')
            return {'sqnr': -100.0, 'avg_exec_cycles': -1, 'tot_sim_cycles': -1}

    def _compute_objectives_and_constraints(self, results):
        """Delegated to energyObjective.energy_objectives:
            [ energy_pJ_per_transform, sqnr_error^2, norm_latency ]
        Area is a constraint now, not an objective - it takes only three values
        over the whole chromosome space, so it carried no search signal."""
        return energy_objectives(results)

    def _save_solution_result(self, sol_id, chromosome, results):
        result_file = os.path.join(RESULTS_DIR, f"gen{CURRENT_GEN}_sol{sol_id}.txt")
        stats = self.template_gen.analyze_chromosome_statistics(chromosome)

        avg_exec = results.get('avg_exec_cycles', -1)
        tot_sim  = results.get('tot_sim_cycles',  -1)

        with open(result_file, 'w') as f:
            f.write(f"FFT Size          : {self.fft_size}\n")
            f.write(f"Generation        : {CURRENT_GEN}\n")
            f.write(f"Solution ID       : {sol_id}\n")
            f.write(f"Chromosome        : {[int(x) for x in chromosome]}\n\n")
            f.write(f"Results:\n")
            f.write(f"  Energy/transform  : {results.get('energy_pj', -1.0):.1f} pJ\n")
            f.write(f"  Dynamic Power     : {results.get('dyn_power_w_used', 0.0):.6f} W\n")
            f.write(f"  Dyn (vectorless)  : {results.get('dyn_vectorless_w') }\n")
            f.write(f"  SAIF applied      : {results.get('saif_used', 0)}\n")
            f.write(f"  Synth chksum match: {results.get('checksum_match', 0)}\n")
            f.write(f"  Total Power       : {results['power']:.6f} W\n")
            f.write(f"  Area              : {results['area']} LUTs\n")
            f.write(f"  LUTRAMs           : {results.get('lutram', 0)}\n")
            f.write(f"  DSPs              : {results.get('dsp', 0)}\n")
            f.write(f"  BRAMs             : {results.get('bram', 0)}\n")
            f.write(f"  FFs               : {results.get('ff', 0)}\n")
            f.write(f"  SQNR              : {results['sqnr']:.2f} dB\n")
            f.write(f"  Crit Path Delay   : {results.get('crit_delay_ns', 0):.3f} ns\n")
            f.write(f"  Norm Latency      : {results.get('norm_latency', 0):.4f}x\n")
            f.write(f"  Avg Exec Cycles   : {avg_exec}\n")
            f.write(f"  Tot Sim Cycles    : {tot_sim}\n")
            f.write(f"\nPrecision Stats:\n")
            for k, v in stats.items():
                if not isinstance(v, list):
                    f.write(f"  {k}: {v}\n")