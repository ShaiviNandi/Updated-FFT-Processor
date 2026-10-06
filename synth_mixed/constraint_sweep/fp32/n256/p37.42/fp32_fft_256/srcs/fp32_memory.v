// =============================================================================
// FP32 FFT Memory Subsystem -- REGISTER-ARRAY VARIANT (FPGA track)
//
// Behavioural true-dual-port register arrays, which Vivado infers as BRAM.
// This is the FPGA-verification implementation; source/mem_sram/fp32_memory.v
// is the ASIC one, built from sram_512x64_2rw macros. Both present the SAME
// module name (fp32_dual_bank_memory_concurrent) and the SAME port list, so
// exactly one is compiled and the cores never change:
//
//     --memory regarray   -> this file            (FPGA / Vivado, BRAM)
//     --memory sram       -> source/mem_sram/...  (ASIC / Yosys + OpenSTA)
//
// Derived from fp16_baseline/source/mem_regarray/fp16_memory.v by widening the
// word 32 -> 64 bits; that file in turn mirrors mixed_dual_bank_memory_concurrent
// in verilog_sources/memory.v -- same dual-bank ping-pong, same sub-bank split,
// same address-compression formulas, same 1-cycle read.
//
// Unlike the macro variant this needs no avoid_double_write guard: that guard
// exists to protect the SRAM macro from a modelled write-write collision, and
// a register array has no such model.
//
// Word format: [63:32] FP32 Real, [31:0] FP32 Imag
// =============================================================================
`timescale 1ns/1ps

module fp32_dual_bank_memory_concurrent #(
    parameter n          = 1024,
    parameter ADDR_WIDTH = 11
)(
    input  wire                  clk,
    input  wire                  rst,

    input  wire                  bank_pingpong,
    input  wire [ADDR_WIDTH-1:0] stage_mask,

    input  wire [ADDR_WIDTH-1:0] rd_addr_a,
    input  wire [ADDR_WIDTH-1:0] rd_addr_b,
    output wire [63:0]           rd_data_a,
    output wire [63:0]           rd_data_b,

    input  wire                  wr_en,
    input  wire [ADDR_WIDTH-1:0] wr_addr_a,
    input  wire [ADDR_WIDTH-1:0] wr_addr_b,
    input  wire [63:0]           wr_data_a,
    input  wire [63:0]           wr_data_b,

    input  wire                  bank_pingpong_wr,
    input  wire [ADDR_WIDTH-1:0] stage_mask_wr
);

    // Safe sizing logic to prevent Vivado crashes on FFT-2 and FFT-4
    localparam SUB_DEPTH = (n <= 2) ? 1 : n / 2;
    localparam MEM_AW    = (n <= 2) ? 1 : $clog2(n/2);

    // Avoiding Dual-Port BRAM mapping errors
    reg [63:0] b0_sub0 [0:SUB_DEPTH-1];
    reg [63:0] b0_sub1 [0:SUB_DEPTH-1];
    reg [63:0] b1_sub0 [0:SUB_DEPTH-1];
    reg [63:0] b1_sub1 [0:SUB_DEPTH-1];

    // -------------------------------------------------------------------------
    // Fallback Resolution Logic Matrix
    // -------------------------------------------------------------------------
    wire is_wr_bank_floating = (bank_pingpong_wr === 1'bz || bank_pingpong_wr === 1'bx);
    wire actual_wr_bank      = is_wr_bank_floating ? bank_pingpong : bank_pingpong_wr;

    wire is_wr_mask_floating = (^stage_mask_wr === 1'bx);
    wire [ADDR_WIDTH-1:0] actual_wr_mask = is_wr_mask_floating ? stage_mask : stage_mask_wr;

    // Sub-bank routing selection
    wire read_sub_sel_a  = |(rd_addr_a & stage_mask);
    wire read_sub_sel_b  = |(rd_addr_b & stage_mask);
    wire write_sub_sel_a = |(wr_addr_a & actual_wr_mask);
    wire write_sub_sel_b = |(wr_addr_b & actual_wr_mask);

    // Address compression formulas
    wire [ADDR_WIDTH-1:0] rd_lower_mask = stage_mask - 1'b1;
    wire [ADDR_WIDTH-1:0] rd_upper_mask = ~rd_lower_mask;
    wire [ADDR_WIDTH-2:0] c_rd_addr_a = (rd_addr_a & rd_lower_mask) | ((rd_addr_a & (rd_upper_mask << 1)) >> 1);
    wire [ADDR_WIDTH-2:0] c_rd_addr_b = (rd_addr_b & rd_lower_mask) | ((rd_addr_b & (rd_upper_mask << 1)) >> 1);

    wire [ADDR_WIDTH-1:0] wr_lower_mask = actual_wr_mask - 1'b1;
    wire [ADDR_WIDTH-1:0] wr_upper_mask = ~wr_lower_mask;
    wire [ADDR_WIDTH-2:0] c_wr_addr_a = (wr_addr_a & wr_lower_mask) | ((wr_addr_a & (wr_upper_mask << 1)) >> 1);
    wire [ADDR_WIDTH-2:0] c_wr_addr_b = (wr_addr_b & wr_lower_mask) | ((wr_addr_b & (wr_upper_mask << 1)) >> 1);

    // =========================================================================
    // PERFECT TRUE DUAL-PORT (TDP) MEMORY INFERENCE
    // =========================================================================

    reg [63:0] r_b0_sub0_a, r_b0_sub1_a, r_b1_sub0_a, r_b1_sub1_a;
    reg [63:0] r_b0_sub0_b, r_b0_sub1_b, r_b1_sub0_b, r_b1_sub1_b;

    // Bank 0, Sub-Bank 0
    wire [ADDR_WIDTH-2:0] b0_sub0_addr_a = (actual_wr_bank == 1'b1) ? c_wr_addr_a : c_rd_addr_a;
    wire                  b0_sub0_we_a   = wr_en & (actual_wr_bank == 1'b1) & (!write_sub_sel_a);
    wire [ADDR_WIDTH-2:0] b0_sub0_addr_b = (actual_wr_bank == 1'b1) ? c_wr_addr_b : c_rd_addr_b;
    wire                  b0_sub0_we_b   = wr_en & (actual_wr_bank == 1'b1) & (!write_sub_sel_b);

    // Safe index slicing
    wire [MEM_AW-1:0] safe_b0_sub0_addr_a = (n <= 2) ? 1'b0 : b0_sub0_addr_a[MEM_AW-1:0];
    wire [MEM_AW-1:0] safe_b0_sub0_addr_b = (n <= 2) ? 1'b0 : b0_sub0_addr_b[MEM_AW-1:0];

    always @(posedge clk) begin // Port A
        if (b0_sub0_we_a) b0_sub0[safe_b0_sub0_addr_a] <= wr_data_a;
        r_b0_sub0_a <= b0_sub0[safe_b0_sub0_addr_a];
    end
    always @(posedge clk) begin // Port B
        if (b0_sub0_we_b) b0_sub0[safe_b0_sub0_addr_b] <= wr_data_b;
        r_b0_sub0_b <= b0_sub0[safe_b0_sub0_addr_b];
    end

    // Bank 0, Sub-Bank 1
    wire [ADDR_WIDTH-2:0] b0_sub1_addr_a = (actual_wr_bank == 1'b1) ? c_wr_addr_a : c_rd_addr_a;
    wire                  b0_sub1_we_a   = wr_en & (actual_wr_bank == 1'b1) & (write_sub_sel_a);
    wire [ADDR_WIDTH-2:0] b0_sub1_addr_b = (actual_wr_bank == 1'b1) ? c_wr_addr_b : c_rd_addr_b;
    wire                  b0_sub1_we_b   = wr_en & (actual_wr_bank == 1'b1) & (write_sub_sel_b);

    wire [MEM_AW-1:0] safe_b0_sub1_addr_a = (n <= 2) ? 1'b0 : b0_sub1_addr_a[MEM_AW-1:0];
    wire [MEM_AW-1:0] safe_b0_sub1_addr_b = (n <= 2) ? 1'b0 : b0_sub1_addr_b[MEM_AW-1:0];

    always @(posedge clk) begin // Port A
        if (b0_sub1_we_a) b0_sub1[safe_b0_sub1_addr_a] <= wr_data_a;
        r_b0_sub1_a <= b0_sub1[safe_b0_sub1_addr_a];
    end
    always @(posedge clk) begin // Port B
        if (b0_sub1_we_b) b0_sub1[safe_b0_sub1_addr_b] <= wr_data_b;
        r_b0_sub1_b <= b0_sub1[safe_b0_sub1_addr_b];
    end

    // Bank 1, Sub-Bank 0
    wire [ADDR_WIDTH-2:0] b1_sub0_addr_a = (actual_wr_bank == 1'b0) ? c_wr_addr_a : c_rd_addr_a;
    wire                  b1_sub0_we_a   = wr_en & (actual_wr_bank == 1'b0) & (!write_sub_sel_a);
    wire [ADDR_WIDTH-2:0] b1_sub0_addr_b = (actual_wr_bank == 1'b0) ? c_wr_addr_b : c_rd_addr_b;
    wire                  b1_sub0_we_b   = wr_en & (actual_wr_bank == 1'b0) & (!write_sub_sel_b);

    wire [MEM_AW-1:0] safe_b1_sub0_addr_a = (n <= 2) ? 1'b0 : b1_sub0_addr_a[MEM_AW-1:0];
    wire [MEM_AW-1:0] safe_b1_sub0_addr_b = (n <= 2) ? 1'b0 : b1_sub0_addr_b[MEM_AW-1:0];

    always @(posedge clk) begin // Port A
        if (b1_sub0_we_a) b1_sub0[safe_b1_sub0_addr_a] <= wr_data_a;
        r_b1_sub0_a <= b1_sub0[safe_b1_sub0_addr_a];
    end
    always @(posedge clk) begin // Port B
        if (b1_sub0_we_b) b1_sub0[safe_b1_sub0_addr_b] <= wr_data_b;
        r_b1_sub0_b <= b1_sub0[safe_b1_sub0_addr_b];
    end

    // Bank 1, Sub-Bank 1
    wire [ADDR_WIDTH-2:0] b1_sub1_addr_a = (actual_wr_bank == 1'b0) ? c_wr_addr_a : c_rd_addr_a;
    wire                  b1_sub1_we_a   = wr_en & (actual_wr_bank == 1'b0) & (write_sub_sel_a);
    wire [ADDR_WIDTH-2:0] b1_sub1_addr_b = (actual_wr_bank == 1'b0) ? c_wr_addr_b : c_rd_addr_b;
    wire                  b1_sub1_we_b   = wr_en & (actual_wr_bank == 1'b0) & (write_sub_sel_b);

    wire [MEM_AW-1:0] safe_b1_sub1_addr_a = (n <= 2) ? 1'b0 : b1_sub1_addr_a[MEM_AW-1:0];
    wire [MEM_AW-1:0] safe_b1_sub1_addr_b = (n <= 2) ? 1'b0 : b1_sub1_addr_b[MEM_AW-1:0];

    always @(posedge clk) begin // Port A
        if (b1_sub1_we_a) b1_sub1[safe_b1_sub1_addr_a] <= wr_data_a;
        r_b1_sub1_a <= b1_sub1[safe_b1_sub1_addr_a];
    end
    always @(posedge clk) begin // Port B
        if (b1_sub1_we_b) b1_sub1[safe_b1_sub1_addr_b] <= wr_data_b;
        r_b1_sub1_b <= b1_sub1[safe_b1_sub1_addr_b];
    end

    // =========================================================================
    // Control Signal Delay & 1-CYCLE Combinational Output Multiplexing
    // =========================================================================

    reg pipe_bank_pingpong;
    reg pipe_read_sub_sel_a;
    reg pipe_read_sub_sel_b;

    always @(posedge clk) begin
        pipe_bank_pingpong  <= bank_pingpong;
        pipe_read_sub_sel_a <= read_sub_sel_a;
        pipe_read_sub_sel_b <= read_sub_sel_b;
    end

    assign rd_data_a = (pipe_bank_pingpong == 1'b0) ?
                       (pipe_read_sub_sel_a ? r_b0_sub1_a : r_b0_sub0_a) :
                       (pipe_read_sub_sel_a ? r_b1_sub1_a : r_b1_sub0_a);

    assign rd_data_b = (pipe_bank_pingpong == 1'b0) ?
                       (pipe_read_sub_sel_b ? r_b0_sub1_b : r_b0_sub0_b) :
                       (pipe_read_sub_sel_b ? r_b1_sub1_b : r_b1_sub0_b);

endmodule
