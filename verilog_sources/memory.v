// =============================================================================
// Mixed-Precision Concurrent FFT Memory Subsystem (II=1 Pipelined Compatible)
// =============================================================================
`timescale 1ns/1ps

module mixed_dual_bank_memory_concurrent #(
    parameter n          = 1024,
    parameter ADDR_WIDTH = 11
)(
    input  wire                  clk,
    input  wire                  rst,             // Active-low async reset

    input  wire                  bank_pingpong,
    input  wire [ADDR_WIDTH-1:0] stage_mask,

    input  wire [ADDR_WIDTH-1:0] rd_addr_a,
    input  wire [ADDR_WIDTH-1:0] rd_addr_b,
    input  wire                  rd_precision,    // 0=FP4, 1=FP8
    output wire [15:0]           rd_data_a,
    output wire [15:0]           rd_data_b,

    input  wire                  wr_en,
    input  wire [ADDR_WIDTH-1:0] wr_addr_a,
    input  wire [ADDR_WIDTH-1:0] wr_addr_b,
    input  wire [23:0]           wr_data_a,
    input  wire [23:0]           wr_data_b
);

    // =========================================================================
    // SUB-BANK HARDWARE INFRASTRUCTURE
    // =========================================================================
    localparam SUB_DEPTH = n / 2;

    (* ram_style = "block" *) reg [23:0] b0_sub0 [0:SUB_DEPTH-1]; // Bank 0 Even
    (* ram_style = "block" *) reg [23:0] b0_sub1 [0:SUB_DEPTH-1]; // Bank 0 Odd
    (* ram_style = "block" *) reg [23:0] b1_sub0 [0:SUB_DEPTH-1]; // Bank 1 Even
    (* ram_style = "block" *) reg [23:0] b1_sub1 [0:SUB_DEPTH-1]; // Bank 1 Odd

    // =========================================================================
    // CONCURRENT SUB-BANK ROUTING SELECTION
    // =========================================================================
    wire read_sub_sel_a  = |(rd_addr_a & stage_mask);
    wire read_sub_sel_b  = |(rd_addr_b & stage_mask);
    wire write_sub_sel_a = |(wr_addr_a & stage_mask);
    wire write_sub_sel_b = |(wr_addr_b & stage_mask);

    // =========================================================================
    // COMBINATIONAL ADDRESS COMPRESSION MATRIX
    // =========================================================================
    wire [ADDR_WIDTH-1:0] lower_mask = stage_mask - 1'b1;
    wire [ADDR_WIDTH-1:0] upper_mask = ~lower_mask;

    wire [ADDR_WIDTH-2:0] c_rd_addr_a = (rd_addr_a & lower_mask) | ((rd_addr_a & (upper_mask << 1)) >> 1);
    wire [ADDR_WIDTH-2:0] c_rd_addr_b = (rd_addr_b & lower_mask) | ((rd_addr_b & (upper_mask << 1)) >> 1);
    wire [ADDR_WIDTH-2:0] c_wr_addr_a = (wr_addr_a & lower_mask) | ((wr_addr_a & (upper_mask << 1)) >> 1);
    wire [ADDR_WIDTH-2:0] c_wr_addr_b = (wr_addr_b & lower_mask) | ((wr_addr_b & (upper_mask << 1)) >> 1);

    // =========================================================================
    // SYNCHRONOUS WRITE PORT EXECUTION
    // =========================================================================
    always @(posedge clk) begin
        if (wr_en) begin
            if (bank_pingpong == 1'b0) begin
                if (!write_sub_sel_a) b1_sub0[c_wr_addr_a] <= wr_data_a;
                else                  b1_sub1[c_wr_addr_a] <= wr_data_a;
                
                if (!write_sub_sel_b) b1_sub0[c_wr_addr_b] <= wr_data_b;
                else                  b1_sub1[c_wr_addr_b] <= wr_data_b;
            end else begin
                if (!write_sub_sel_a) b0_sub0[c_wr_addr_a] <= wr_data_a;
                else                  b0_sub1[c_wr_addr_a] <= wr_data_a;
                
                if (!write_sub_sel_b) b0_sub0[c_wr_addr_b] <= wr_data_b;
                else                  b0_sub1[c_wr_addr_b] <= wr_data_b;
            end
        end
    end

    // =========================================================================
    // PIPELINED READ PORTS (Fixed Synchronization)
    // =========================================================================
    reg [23:0] rd_full_a, rd_full_b;
    reg        rd_prec_d1;
    reg [15:0] out_reg_a, out_reg_b;

    always @(posedge clk) begin
        // Stage 1: BRAM Synchronous Output Fetch
        if (bank_pingpong == 1'b0) begin
            rd_full_a <= (!read_sub_sel_a) ? b0_sub0[c_rd_addr_a] : b0_sub1[c_rd_addr_a];
            rd_full_b <= (!read_sub_sel_b) ? b0_sub0[c_rd_addr_b] : b0_sub1[c_rd_addr_b];
        end else begin
            rd_full_a <= (!read_sub_sel_a) ? b1_sub0[c_rd_addr_a] : b1_sub1[c_rd_addr_a];
            rd_full_b <= (!read_sub_sel_b) ? b1_sub0[c_rd_addr_b] : b1_sub1[c_rd_addr_b];
        end
        
        // Stage 1 Control: Delay precision by exactly ONE cycle to match BRAM read latency
        rd_prec_d1 <= rd_precision;

        // Stage 2: Precision Formatting Logic Matrix
        if (!rst) begin
            out_reg_a <= 16'h0000;
            out_reg_b <= 16'h0000;
        end else begin
            // Evaluate precision perfectly aligned with the arrival of rd_full_a/b
            if (rd_prec_d1) begin
                out_reg_a <= rd_full_a[23:8]; // FP8 Extract
                out_reg_b <= rd_full_b[23:8];
            end else begin
                out_reg_a <= {8'h00, rd_full_a[7:0]}; // FP4 Zero-Extend Extract
                out_reg_b <= {8'h00, rd_full_b[7:0]};
            end
        end
    end

    assign rd_data_a = out_reg_a;
    assign rd_data_b = out_reg_b;

endmodule