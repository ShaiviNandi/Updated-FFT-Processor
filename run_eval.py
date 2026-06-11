"""
System Integration Verification Script
======================================
Automates validation between the Pipelined Template Generator 
and the strict FP32 Performance Evaluator module.
"""

import os
import sys
from fft_template_generator import FFTTemplateGenerator
from performance_evaluator import PerformanceEvaluator

def main():
    print("=================================================================")
    print("RUNNING END-TO-END PIPELINED PROCESSOR VERIFICATION SUITE")
    print("=================================================================")

    # Setup directories
    os.makedirs("./verilog_sources", exist_ok=True)
    os.makedirs("./generated_designs", exist_ok=True)
    os.makedirs("./sim", exist_ok=True)

    fft_size = 16
    print(f"[STEP 1] Initializing Core Factory for N={fft_size}...")
    generator = FFTTemplateGenerator(fft_size=fft_size)

    # 4 stages for N=16. Chromosome length = 8 (2 genes per stage)
    # Binary pattern maps varying FP4 vs FP8 allocation profiles
    chromosome = [1, 1, 1, 1, 1, 1, 1, 1] 
    print(f"         Target Precision Chromosome Map: {chromosome}")

    # Generate pipeline code-blocks
    output_core_path = f"./generated_designs/pipelined_fft_{fft_size}.v"
    core_f, top_f = generator.generate_verilog(chromosome, output_core_path)
    print(f"✓ Success: Core written to -> {core_f}")
    print(f"✓ Success: Top written to  -> {top_f}")

    print(f"\n[STEP 2] Setting up Single-Precision FP32 Evaluator Metrics...")
    evaluator = PerformanceEvaluator(fft_size=fft_size)

    print(f"\n[STEP 3] Launching Compiled Pipelined iverilog Simulations...")
    # Invoke evaluation matrix loops
    avg_sqnr = evaluator.evaluate_design(
        verilog_file=core_f,
        design_name=f"pipelined_fft_{fft_size}",
        chromosome=chromosome
    )

    print("\n=================================================================")
    if avg_sqnr > -50.0:
        print(f"✓ SYSTEM INTEGRATION PASSED! Average System SQNR: {avg_sqnr:.2f} dB")
    else:
        print("✗ SYSTEM INTEGRATION FAILED: Simulation did not return valid metrics.")
    print("=================================================================\n")

if __name__ == "__main__":
    main()