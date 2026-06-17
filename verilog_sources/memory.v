// =============================================================================
// Mixed-Precision Concurrent FFT Memory Subsystem
// PERFECT TRUE DUAL-PORT (TDP) BRAM INFERENCE IMPLEMENTED (1-CYCLE READ)
// =============================================================================
`timescale 1ns/1ps

module mixed_dual_bank_memory_concurrent #(
    parameter n          = 1024,
    parameter ADDR_WIDTH = 11
)(
    input  wire                  clk,
    input  wire                  rst,             

    input  wire                  bank_pingpong,
    input  wire [ADDR_WIDTH-1:0] stage_mask,

    input  wire [ADDR_WIDTH-1:0] rd_addr_a,
    input  wire [ADDR_WIDTH-1:0] rd_addr_b,
    input  wire                  rd_precision,    
    output wire [15:0]           rd_data_a,
    output wire [15:0]           rd_data_b,

    input  wire                  wr_en,
    input  wire [ADDR_WIDTH-1:0] wr_addr_a,
    input  wire [ADDR_WIDTH-1:0] wr_addr_b,
    input  wire [23:0]           wr_data_a,
    input  wire [23:0]           wr_data_b,

    input  wire                  bank_pingpong_wr,
    input  wire [ADDR_WIDTH-1:0] stage_mask_wr
);

    localparam SUB_DEPTH = n / 2;

    (* ram_style = "block" *) reg [23:0] b0_sub0 [0:SUB_DEPTH-1];
    (* ram_style = "block" *) reg [23:0] b0_sub1 [0:SUB_DEPTH-1]; 
    (* ram_style = "block" *) reg [23:0] b1_sub0 [0:SUB_DEPTH-1];
    (* ram_style = "block" *) reg [23:0] b1_sub1 [0:SUB_DEPTH-1]; 

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
    // PERFECT TRUE DUAL-PORT (TDP) BRAM INFERENCE
    // =========================================================================

    reg [23:0] r_b0_sub0_a, r_b0_sub1_a, r_b1_sub0_a, r_b1_sub1_a;
    reg [23:0] r_b0_sub0_b, r_b0_sub1_b, r_b1_sub0_b, r_b1_sub1_b;

    // Bank 0, Sub-Bank 0
    wire [ADDR_WIDTH-2:0] b0_sub0_addr_a = (actual_wr_bank == 1'b1) ? c_wr_addr_a : c_rd_addr_a;
    wire                  b0_sub0_we_a   = wr_en & (actual_wr_bank == 1'b1) & (!write_sub_sel_a);
    wire [ADDR_WIDTH-2:0] b0_sub0_addr_b = (actual_wr_bank == 1'b1) ? c_wr_addr_b : c_rd_addr_b;
    wire                  b0_sub0_we_b   = wr_en & (actual_wr_bank == 1'b1) & (!write_sub_sel_b);

    always @(posedge clk) begin // Port A
        if (b0_sub0_we_a) b0_sub0[b0_sub0_addr_a] <= wr_data_a;
        r_b0_sub0_a <= b0_sub0[b0_sub0_addr_a];
    end
    always @(posedge clk) begin // Port B
        if (b0_sub0_we_b) b0_sub0[b0_sub0_addr_b] <= wr_data_b;
        r_b0_sub0_b <= b0_sub0[b0_sub0_addr_b];
    end

    // Bank 0, Sub-Bank 1
    wire [ADDR_WIDTH-2:0] b0_sub1_addr_a = (actual_wr_bank == 1'b1) ? c_wr_addr_a : c_rd_addr_a;
    wire                  b0_sub1_we_a   = wr_en & (actual_wr_bank == 1'b1) & (write_sub_sel_a);
    wire [ADDR_WIDTH-2:0] b0_sub1_addr_b = (actual_wr_bank == 1'b1) ? c_wr_addr_b : c_rd_addr_b;
    wire                  b0_sub1_we_b   = wr_en & (actual_wr_bank == 1'b1) & (write_sub_sel_b);

    always @(posedge clk) begin // Port A
        if (b0_sub1_we_a) b0_sub1[b0_sub1_addr_a] <= wr_data_a;
        r_b0_sub1_a <= b0_sub1[b0_sub1_addr_a];
    end
    always @(posedge clk) begin // Port B
        if (b0_sub1_we_b) b0_sub1[b0_sub1_addr_b] <= wr_data_b;
        r_b0_sub1_b <= b0_sub1[b0_sub1_addr_b];
    end

    // Bank 1, Sub-Bank 0 
    wire [ADDR_WIDTH-2:0] b1_sub0_addr_a = (actual_wr_bank == 1'b0) ? c_wr_addr_a : c_rd_addr_a;
    wire                  b1_sub0_we_a   = wr_en & (actual_wr_bank == 1'b0) & (!write_sub_sel_a);
    wire [ADDR_WIDTH-2:0] b1_sub0_addr_b = (actual_wr_bank == 1'b0) ? c_wr_addr_b : c_rd_addr_b;
    wire                  b1_sub0_we_b   = wr_en & (actual_wr_bank == 1'b0) & (!write_sub_sel_b);

    always @(posedge clk) begin // Port A
        if (b1_sub0_we_a) b1_sub0[b1_sub0_addr_a] <= wr_data_a;
        r_b1_sub0_a <= b1_sub0[b1_sub0_addr_a];
    end
    always @(posedge clk) begin // Port B
        if (b1_sub0_we_b) b1_sub0[b1_sub0_addr_b] <= wr_data_b;
        r_b1_sub0_b <= b1_sub0[b1_sub0_addr_b];
    end

    // Bank 1, Sub-Bank 1
    wire [ADDR_WIDTH-2:0] b1_sub1_addr_a = (actual_wr_bank == 1'b0) ? c_wr_addr_a : c_rd_addr_a;
    wire                  b1_sub1_we_a   = wr_en & (actual_wr_bank == 1'b0) & (write_sub_sel_a);
    wire [ADDR_WIDTH-2:0] b1_sub1_addr_b = (actual_wr_bank == 1'b0) ? c_wr_addr_b : c_rd_addr_b;
    wire                  b1_sub1_we_b   = wr_en & (actual_wr_bank == 1'b0) & (write_sub_sel_b);

    always @(posedge clk) begin // Port A
        if (b1_sub1_we_a) b1_sub1[b1_sub1_addr_a] <= wr_data_a;
        r_b1_sub1_a <= b1_sub1[b1_sub1_addr_a];
    end
    always @(posedge clk) begin // Port B
        if (b1_sub1_we_b) b1_sub1[b1_sub1_addr_b] <= wr_data_b;
        r_b1_sub1_b <= b1_sub1[b1_sub1_addr_b];
    end

    // =========================================================================
    // Control Signal Delay & 1-CYCLE Combinational Output Multiplexing
    // =========================================================================

    reg pipe_bank_pingpong;
    reg pipe_read_sub_sel_a;
    reg pipe_read_sub_sel_b;
    reg rd_prec_d1;

    always @(posedge clk) begin
        pipe_bank_pingpong  <= bank_pingpong;
        pipe_read_sub_sel_a <= read_sub_sel_a;
        pipe_read_sub_sel_b <= read_sub_sel_b;
        rd_prec_d1          <= rd_precision;
    end

    wire [23:0] rd_full_a = (pipe_bank_pingpong == 1'b0) ?
                            (pipe_read_sub_sel_a ? r_b0_sub1_a : r_b0_sub0_a) :
                            (pipe_read_sub_sel_a ? r_b1_sub1_a : r_b1_sub0_a);

    wire [23:0] rd_full_b = (pipe_bank_pingpong == 1'b0) ? 
                            (pipe_read_sub_sel_b ? r_b0_sub1_b : r_b0_sub0_b) :
                            (pipe_read_sub_sel_b ? r_b1_sub1_b : r_b1_sub0_b);

    // No intermediate register -> Solves the testbench phase/array shift bug
    assign rd_data_a = rd_prec_d1 ? rd_full_a[23:8] : {8'h00, rd_full_a[7:0]};
    assign rd_data_b = rd_prec_d1 ? rd_full_b[23:8] : {8'h00, rd_full_b[7:0]};

endmodule