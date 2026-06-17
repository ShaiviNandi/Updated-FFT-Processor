"""
Multi-Length System Integration Verification Sweep
==================================================
Automates validation between the Pipelined Template Generator 
and the strict FP32 Performance Evaluator module across multiple FFT sizes.

FIX: evaluate_design() returns a dict; extract ['sqnr'] before numeric comparisons.
"""

import os
import math
from fft_template_generator import FFTTemplateGenerator
from performance_evaluator import PerformanceEvaluator

def generate_chromosome_archetypes(fft_size):
    """Dynamically generates target chromosomes based on the required stage depth."""
    num_stages = int(math.log2(fft_size))
    chrom_len = num_stages * 2

    alt_1_0 = [1 if i % 2 == 0 else 0 for i in range(chrom_len)]
    alt_0_1 = [0 if i % 2 == 0 else 1 for i in range(chrom_len)]
    
    half_len = chrom_len // 2

    archetypes = [
        ("Pure FP8",           [1] * chrom_len),
        ("Pure FP4",           [0] * chrom_len),
        ("Front FP8, Back FP4",[1] * half_len + [0] * (chrom_len - half_len)),
        ("Front FP4, Back FP8",[0] * half_len + [1] * (chrom_len - half_len)),
        ("FP8 Mul / FP4 Add",  alt_1_0),
        ("FP4 Mul / FP8 Add",  alt_0_1)
    ]

    if chrom_len >= 6:
        hq_mixed = [1] * chrom_len
        hq_mixed[0] = 0; hq_mixed[1] = 0; hq_mixed[-1] = 0
        archetypes.append(("Squeezed Mixed", hq_mixed))

    return archetypes

def main():
    print("=================================================================")
    print("RUNNING MULTI-LENGTH PIPELINED PROCESSOR VERIFICATION SWEEP")
    print("=================================================================")

    os.makedirs("./verilog_sources", exist_ok=True)
    os.makedirs("./generated_designs", exist_ok=True)
    os.makedirs("./sim", exist_ok=True)

    fft_sizes_to_test = [8, 16, 32]
    master_leaderboards = {}

    for fft_size in fft_sizes_to_test:
        print(f"\n\n{'='*65}")
        print(f"INITIATING SWEEP FOR N = {fft_size}")
        print(f"{'='*65}")

        generator = FFTTemplateGenerator(fft_size=fft_size)
        evaluator = PerformanceEvaluator(fft_size=fft_size)

        chromosomes_to_test = generate_chromosome_archetypes(fft_size)
        results = []

        print(f"\n[STEP 1] Launching Sweep over {len(chromosomes_to_test)} configurations...\n")

        for i, (label, chromosome) in enumerate(chromosomes_to_test):
            print(f"--- N={fft_size} | ITERATION {i+1}/{len(chromosomes_to_test)}: {label} ---")
            print(f"Target Precision Map: {chromosome}")

            output_core_path = f"./generated_designs/pipelined_fft_{fft_size}_sweep_{i}.v"
            core_f, top_f = generator.generate_verilog(chromosome, output_core_path)

            # FIX: evaluate_design() returns a dict — extract scalar fields explicitly
            perf = evaluator.evaluate_design(
                verilog_file=core_f,
                design_name=f"pipelined_fft_{fft_size}_sweep_{i}",
                chromosome=chromosome
            )
            avg_sqnr       = perf['sqnr']            # float
            avg_exec_cycles = perf['avg_exec_cycles'] # int
            tot_sim_cycles  = perf['tot_sim_cycles']  # int

            results.append((label, chromosome, avg_sqnr, avg_exec_cycles, tot_sim_cycles))

            if avg_sqnr > -50.0:
                print(f"Iteration Passed! Avg System SQNR: {avg_sqnr:.2f} dB  "
                      f"| ExecCycles: {avg_exec_cycles}  TotCycles: {tot_sim_cycles}\n")
            else:
                print("Iteration Failed: Simulation did not return valid metrics.\n")

        # Sort by SQNR descending
        results.sort(key=lambda x: x[2], reverse=True)
        master_leaderboards[fft_size] = results

        print(f"=================================================================")
        print(f"SWEEP RESULTS LEADERBOARD: N = {fft_size}")
        print(f"=================================================================")
        for rank, (label, chrom, sqnr, exec_c, tot_c) in enumerate(results, 1):
            print(f"Rank {rank:02d} | {label:<20} | Map: {chrom} | "
                  f"Avg SQNR: {sqnr:>7.2f} dB | ExecCyc: {exec_c} | TotCyc: {tot_c}")
        print(f"=================================================================\n")

    print("\n\nMASTER SUMMARY")
    for size in fft_sizes_to_test:
        top_mixed = next((r for r in master_leaderboards[size] if r[0] != "Pure FP8"), None)
        pure_fp8  = next((r for r in master_leaderboards[size] if r[0] == "Pure FP8"), None)
        
        print(f"\nN = {size}:")
        if pure_fp8:
            print(f"  Ceiling (Pure FP8): {pure_fp8[2]:.2f} dB")
        if top_mixed:
            print(f"  Best Mixed Config : {top_mixed[2]:.2f} dB -> {top_mixed[0]} {top_mixed[1]}")

if __name__ == "__main__":
    main()