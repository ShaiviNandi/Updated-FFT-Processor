"""
Multi-Length System Integration Verification Sweep
==================================================
Automates validation between the Pipelined Template Generator 
and the strict FP32 Performance Evaluator module across multiple FFT sizes.
"""

import os
import math
from fft_template_generator import FFTTemplateGenerator
from performance_evaluator import PerformanceEvaluator

def generate_chromosome_archetypes(fft_size):
    """Dynamically generates target chromosomes based on the required stage depth."""
    num_stages = int(math.log2(fft_size))
    chrom_len = num_stages * 2

    # Helper for generating alternating patterns
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

    # Add a custom "High Quality Mixed" (mostly 1s, with a few 0s at the start/end)
    if chrom_len >= 6:
        hq_mixed = [1] * chrom_len
        hq_mixed[0] = 0; hq_mixed[1] = 0; hq_mixed[-1] = 0 # Example squeeze
        archetypes.append(("Squeezed Mixed", hq_mixed))

    return archetypes

def main():
    print("=================================================================")
    print("RUNNING MULTI-LENGTH PIPELINED PROCESSOR VERIFICATION SWEEP")
    print("=================================================================")

    # Setup directories
    os.makedirs("./verilog_sources", exist_ok=True)
    os.makedirs("./generated_designs", exist_ok=True)
    os.makedirs("./sim", exist_ok=True)

    # Define the FFT sizes you want to sweep across
    fft_sizes_to_test = [8, 16, 32]
    
    # Store all leaderboards for a final master summary
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

            # Generate pipeline code-blocks
            output_core_path = f"./generated_designs/pipelined_fft_{fft_size}_sweep_{i}.v"
            core_f, top_f = generator.generate_verilog(chromosome, output_core_path)

            # Invoke evaluation matrix
            avg_sqnr = evaluator.evaluate_design(
                verilog_file=core_f,
                design_name=f"pipelined_fft_{fft_size}_sweep_{i}",
                chromosome=chromosome
            )

            # Record and display the iteration result
            results.append((label, chromosome, avg_sqnr))
            if avg_sqnr > -50.0:
                print(f"Iteration Passed! Avg System SQNR: {avg_sqnr:.2f} dB\n")
            else:
                print("Iteration Failed: Simulation did not return valid metrics.\n")

        # Sort results from highest SQNR to lowest
        results.sort(key=lambda x: x[2], reverse=True)
        master_leaderboards[fft_size] = results

        # ---------------------------------------------------------
        # Per-Size Leaderboard
        # ---------------------------------------------------------
        print(f"=================================================================")
        print(f"SWEEP RESULTS LEADERBOARD: N = {fft_size}")
        print(f"=================================================================")
        for rank, (label, chrom, sqnr) in enumerate(results, 1):
            print(f"Rank {rank:02d} | {label:<20} | Map: {chrom} | Avg SQNR: {sqnr:>7.2f} dB")
        print(f"=================================================================\n")

    # ---------------------------------------------------------
    # Master Summary
    # ---------------------------------------------------------
    print("\n\nMASTER SUMMARY")
    for size in fft_sizes_to_test:
        top_mixed = next((r for r in master_leaderboards[size] if r[0] != "Pure FP8"), None)
        pure_fp8 = next((r for r in master_leaderboards[size] if r[0] == "Pure FP8"), None)
        
        print(f"\nN = {size}:")
        if pure_fp8:
            print(f"  Ceiling (Pure FP8): {pure_fp8[2]:.2f} dB")
        if top_mixed:
            print(f"  Best Mixed Config : {top_mixed[2]:.2f} dB -> {top_mixed[0]} {top_mixed[1]}")

if __name__ == "__main__":
    main()