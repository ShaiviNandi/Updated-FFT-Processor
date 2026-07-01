#!/usr/bin/env python3
import os
import subprocess
import sys
import glob
import numpy as np
import shutil

# Adjust python load path dynamically to import from both the script's folder and the root folder
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR) 

sys.path.append(SCRIPT_DIR)
sys.path.append(ROOT_DIR)

from rv32i_asm import assemble
from performance_evaluator import PerformanceEvaluator, SIGNAL_LABELS

def generate_bulletproof_wrapper():
    """Injects an explicitly pipelined FSM handling the asymmetric memory requirements."""
    wrapper_code = """`timescale 1ns/1ps
module fft_pcpi_wrapper (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        pcpi_valid,
    input  wire [31:0] pcpi_insn,
    input  wire [31:0] pcpi_rs1,
    input  wire [31:0] pcpi_rs2,
    output reg         pcpi_wr,
    output reg  [31:0] pcpi_rd,
    output reg         pcpi_wait,
    output reg         pcpi_ready
);
    wire is_custom0 = pcpi_valid && (pcpi_insn[6:0] == 7'b0001011);
    wire [2:0] f3 = pcpi_insn[14:12];

    reg         fft_start;
    wire        fft_done;
    reg         fft_load_en;
    reg  [7:0]  fft_load_addr;
    reg  [15:0] fft_load_data;
    reg         fft_unload_en;
    reg  [7:0]  fft_unload_addr;
    wire [15:0] fft_unload_data;

    mixed_fft_256_top u_fft (
        .clk(clk), .rst(rst_n), .start(fft_start), .done(fft_done),
        .load_en(fft_load_en), .load_addr(fft_load_addr), .load_data(fft_load_data),
        .unload_en(fft_unload_en), .unload_addr(fft_unload_addr), .unload_data(fft_unload_data)
    );

    reg status_done;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) status_done <= 0;
        else if (fft_start) status_done <= 0;
        else if (fft_done) status_done <= 1;
    end

    reg [2:0] state;
    reg [31:0] timeout;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= 0; pcpi_ready <= 0; pcpi_wr <= 0; pcpi_rd <= 0; pcpi_wait <= 0;
            fft_start <= 0; fft_load_en <= 0; fft_unload_en <= 0;
        end else begin
            // Default drops to prevent stalls
            pcpi_ready <= 0; pcpi_wr <= 0; pcpi_wait <= 0;
            fft_start <= 0; fft_load_en <= 0; fft_unload_en <= 0;

            if (state == 0) begin
                if (is_custom0 && !pcpi_ready) begin
                    pcpi_wait <= 1;
                    if (f3 == 3'b001) begin // LOAD: 1-cycle pulse
                        fft_load_en <= 1; fft_load_addr <= pcpi_rs2[7:0]; fft_load_data <= pcpi_rs1[15:0];
                        state <= 1; 
                    end else if (f3 == 3'b010) begin // STORE: Initiate multi-cycle hold
                        fft_unload_en <= 1; fft_unload_addr <= pcpi_rs1[7:0];
                        state <= 2; 
                    end else if (f3 == 3'b011) begin // START: 1-cycle pulse
                        fft_start <= 1; state <= 1;
                    end else if (f3 == 3'b100) begin // WAIT
                        timeout <= pcpi_rs1; state <= 5;
                    end else if (f3 == 3'b101) begin // STATUS
                        pcpi_ready <= 1; pcpi_wr <= 1; pcpi_rd <= {31'b0, status_done};
                    end
                end
            end
            else if (state == 1) begin // Handshake complete for 1-cycle operations
                pcpi_ready <= 1; state <= 0;
            end
            else if (state == 2) begin // STORE Pipeline Cycle 2
                pcpi_wait <= 1; fft_unload_en <= 1; fft_unload_addr <= pcpi_rs1[7:0];
                state <= 3;
            end
            else if (state == 3) begin // STORE Pipeline Cycle 3
                pcpi_wait <= 1; fft_unload_en <= 1; fft_unload_addr <= pcpi_rs1[7:0];
                state <= 4;
            end
            else if (state == 4) begin // STORE Capture Data
                pcpi_ready <= 1; pcpi_wr <= 1; pcpi_rd <= {16'b0, fft_unload_data};
                state <= 0;
            end
            else if (state == 5) begin // Polling wait loop
                pcpi_wait <= 1;
                if (status_done) begin
                    pcpi_ready <= 1; pcpi_wr <= 1; pcpi_rd <= 1; state <= 0;
                end else if (timeout == 1) begin
                    pcpi_ready <= 1; pcpi_wr <= 1; pcpi_rd <= 0; state <= 0;
                end else begin
                    timeout <= timeout - 1;
                end
            end
        end
    end
endmodule
"""
    wrapper_path = os.path.abspath("./risc-v-integration/fft_pcpi_wrapper.v")
    os.makedirs(os.path.dirname(wrapper_path), exist_ok=True)
    with open(wrapper_path, "w") as f:
        f.write(wrapper_code)

def build_firmware_hex(pe, asm_file, out_hex):
    with open(asm_file, 'r') as f:
        lines = f.readlines()
    words = assemble(lines)
    firmware = ["00000013"] * 8192 
    for i, w in enumerate(words):
        firmware[i] = f"{w:08x}"
    base_idx = 2048
    for ti, vec in enumerate(pe.test_vectors):
        for si, sample in enumerate(vec):
            re_fp8 = pe.float_to_fp8_e4m3(sample.real) & 0xFF
            im_fp8 = pe.float_to_fp8_e4m3(sample.imag) & 0xFF
            packed_word = (re_fp8 << 8) | im_fp8
            firmware[base_idx + (ti * 256) + si] = f"{packed_word:08x}"
    os.makedirs(os.path.dirname(out_hex), exist_ok=True)
    with open(out_hex, 'w') as f:
        f.write("\n".join(firmware) + "\n")

def run_soc_evaluation():
    n = 256
    num_tests = 11
    
    sim_dir = os.path.abspath("./sim")
    asm_path = os.path.abspath("./risc-v-integration/fft_batch_test.asm")
    firmware_hex_path = os.path.join(sim_dir, "firmware.hex")
    results_hex_path = os.path.join(sim_dir, "results.hex")
    
    if os.path.exists(results_hex_path):
        os.remove(results_hex_path)
    
    print("[1] Initializing Evaluator and Generating Test Vectors...")
    pe = PerformanceEvaluator(n)
    
    print("[2] Building unified firmware.hex...")
    build_firmware_hex(pe, asm_path, firmware_hex_path)
    generate_bulletproof_wrapper()
    
    print("[3] Brute-forcing Twiddle ROM Paths...")
    base_twiddle = pe._write_twiddle_file(sim_dir)
    os.makedirs(os.path.join(sim_dir, 'rtl'), exist_ok=True)
    os.makedirs(os.path.join(sim_dir, 'verilog_sources'), exist_ok=True)
    shutil.copy(base_twiddle, os.path.join(sim_dir, 'rtl', 'twiddles_1024.txt'))
    shutil.copy(base_twiddle, os.path.join(sim_dir, 'verilog_sources', 'twiddles_1024.txt'))
    shutil.copy(base_twiddle, os.path.abspath('twiddles_1024.txt'))
    
    if not os.path.exists("./picorv32.v"):
        print("[4] Core Missing! Fetching pristine picorv32.v core...")
        subprocess.run(["curl", "-s", "-o", "./picorv32.v", "https://raw.githubusercontent.com/YosysHQ/picorv32/master/picorv32.v"], check=True)
    
    print("[5] Compiling Verilog SoC via Icarus...")
    compile_cmd = [
        'iverilog', '-o', os.path.join(sim_dir, 'fft_soc.vvp'), 
        '-I', './verilog_sources', 
        '-I', './generated_cores/fft_256_sol_28',
        '-g2012',
        './risc-v-integration/tb_fft_soc.v',      
        './risc-v-integration/picorv32_fft_soc.v',  
        './risc-v-integration/fft_pcpi_wrapper.v',   
        './generated_cores/fft_256_sol_28/mixed_fft_256_top.v', 
        'picorv32.v'
    ]
    
    lib_sources = glob.glob('./verilog_sources/*.v')
    lib_sources.extend(glob.glob('./generated_cores/fft_256_sol_28/*.v'))
    for f in lib_sources:
        if 'test' not in os.path.basename(f) and 'mixed_fft_256_top.v' not in os.path.basename(f):
            compile_cmd.append(f)
            
    res = subprocess.run(compile_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print("\n[COMPILE ERROR] iverilog pipeline construction broken:\n", res.stderr)
        return

    print("[6] Running Simulation Engine...")
    sim_res = subprocess.run(['vvp', 'fft_soc.vvp'], capture_output=True, text=True, cwd=sim_dir)
    
    if sim_res.returncode != 0 or not os.path.exists(results_hex_path):
        print(f"\n[FATAL SIMULATION ERROR]\n{sim_res.stderr}\n{sim_res.stdout}")
        return
    
    print("[7] Processing Output Vectors & Processing SQNR Matrix Verification...")
    goldens = pe._compute_golden_outputs()
    hw_outputs = []
        
    with open(results_hex_path, 'r') as f:
        for line in f:
            line_str = line.strip()
            if line_str:
                word = int(line_str, 16)
                re_float = pe.fp8_to_float((word >> 8) & 0xFF)
                im_float = pe.fp8_to_float(word & 0xFF)
                hw_outputs.append(re_float + 1j * im_float)
            
    hw_outputs = np.array(hw_outputs, dtype=np.complex64)
    print("\n=======================================================")
    print("  SoC Firmware Executed Metrics Breakdown - FFT-256")
    print("=======================================================")
    total_sqnr = 0.0
    for i in range(num_tests):
        approx = hw_outputs[i * n : (i + 1) * n]
        golden = goldens[i]
        label = SIGNAL_LABELS[i]
        sqnr = pe.calculate_sqnr(golden, approx, final_stage_is_fp8=True)
        print(f"  {label:<25}  {sqnr:>10.2f} dB")
        total_sqnr += sqnr
    print("-------------------------------------------------------")
    print(f"  Average System SQNR:        {total_sqnr/num_tests:>10.2f} dB")
    print("=======================================================")

if __name__ == "__main__":
    run_soc_evaluation()