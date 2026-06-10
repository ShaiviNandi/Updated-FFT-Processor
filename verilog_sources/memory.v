// =============================================================================
// Mixed-Precision Concurrent FFT Memory Subsystem (Fully Backward-Compatible)
// FIXED: Verilog-2001 syntax compatible port list with internal fallback logic.
// =============================================================================
`timescale 1ns/1ps

module mixed_dual_bank_memory_concurrent #(
    parameter n          = 1024,
    parameter ADDR_WIDTH = 11
)(
    input  wire                  clk,
    input  wire                  rst,             

    // Ports expected by standalone memory_tb.v
    input  wire                  bank_pingpong,
    input  wire [ADDR_WIDTH-1:0] stage_mask,

    // Concurrent Read Channels
    input  wire [ADDR_WIDTH-1:0] rd_addr_a,
    input  wire [ADDR_WIDTH-1:0] rd_addr_b,
    input  wire                  rd_precision,    
    output wire [15:0]           rd_data_a,
    output wire [15:0]           rd_data_b,

    // Concurrent Write Channels
    input  wire                  wr_en,
    input  wire [ADDR_WIDTH-1:0] wr_addr_a,
    input  wire [ADDR_WIDTH-1:0] wr_addr_b,
    input  wire [23:0]           wr_data_a,
    input  wire [23:0]           wr_data_b,

    // Optional Pipeline Overrides (Declared as normal clean inputs)
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
    // If the pipeline overrides are left unconnected in a module instantiation,
    // Verilog defaults their values to 'z' (high-impedance). We check for 'z'
    // or 'x' and seamlessly fall back to the standard memory_tb ports!
    // -------------------------------------------------------------------------
    wire actual_wr_bank  = (bank_pingpong_wr === 1'bz || bank_pingpong_wr === 1'bx) ? bank_pingpong : bank_pingpong_wr;
    wire [ADDR_WIDTH-1:0] actual_wr_mask = (stage_mask_wr[0] === 1'bz || stage_mask_wr[0] === 1'bx) ? stage_mask : stage_mask_wr;

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

    // Synchronous Write Interface
    always @(posedge clk) begin
        if (wr_en) begin
            if (actual_wr_bank == 1'b0) begin
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

    // Pipelined Read Interface
    reg [23:0] rd_full_a, rd_full_b;
    reg        rd_prec_d1;
    reg [15:0] out_reg_a, out_reg_b;

    always @(posedge clk) begin
        if (bank_pingpong == 1'b0) begin
            rd_full_a <= (!read_sub_sel_a) ? b0_sub0[c_rd_addr_a] : b0_sub1[c_rd_addr_a];
            rd_full_b <= (!read_sub_sel_b) ? b0_sub0[c_rd_addr_b] : b0_sub1[c_rd_addr_b];
        end else begin
            rd_full_a <= (!read_sub_sel_a) ? b1_sub0[c_rd_addr_a] : b1_sub1[c_rd_addr_a];
            rd_full_b <= (!read_sub_sel_b) ? b1_sub0[c_rd_addr_b] : b1_sub1[c_rd_addr_b];
        end
        rd_prec_d1 <= rd_precision;

        if (!rst) begin
            out_reg_a <= 16'h0000; out_reg_b <= 16'h0000;
        end else begin
            if (rd_prec_d1) begin
                out_reg_a <= rd_full_a[23:8];
                out_reg_b <= rd_full_b[23:8];
            end else begin
                out_reg_a <= {8'h00, rd_full_a[7:0]};
                out_reg_b <= {8'h00, rd_full_b[7:0]};
            end
        end
    end

    assign rd_data_a = out_reg_a;
    assign rd_data_b = out_reg_b;

endmodule