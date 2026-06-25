// =============================================================================
// Mixed-Precision FFT TOP - 16-point PIPELINED CONFIGURATION
// =============================================================================
`timescale 1ns/1ps

module pipelined_fft_16_sweep_4_top (
    input  wire        clk,
    input  wire        rst,     

    input  wire        start,
    output reg         done,

    input  wire              load_en,
    input  wire [3:0]  load_addr,
    input  wire [15:0]       load_data,

    input  wire              unload_en,
    input  wire [3:0]  unload_addr,
    output wire [15:0]       unload_data
);

    wire [10:0] load_addr_rev;

    bit_reverse #(
        .MAX_N(1024),
        .WIDTH(11)
    ) br (
        .in  ({{ 7'b0, load_addr }}),
        .N   (11'd16),
        .out (load_addr_rev)
    );

    reg bank_sel;
    wire core_done;
    wire [15:0] core_rd_data;

    wire [7:0] load_fp4;
    complex_fp8_to_fp4 load_fmt_conv (
        .complex_fp8 (load_data),
        .complex_fp4 (load_fp4)
    );
    wire [23:0] load_data_24 = {load_data, load_fp4};

    pipelined_fft_16_sweep_4_core #(
        .MAX_N     (1024),
        .ADDR_WIDTH(11)
    ) core (
        .clk          (clk),
        .rst          (rst),
        .start        (start),
        .done         (core_done),

        .ext_wr_en    (load_en),
        .ext_wr_addr  (load_addr_rev),
        .ext_wr_data  (load_data_24),

        .ext_reading  (unload_en),
        .ext_rd_addr  ({{ 7'b0, unload_addr }}),
        .ext_rd_data  (core_rd_data),

        .ext_bank_sel (bank_sel)
    );

    assign unload_data = core_rd_data;

    always @(posedge clk or negedge rst) begin
        if (!rst) begin
            done     <= 1'b0;
            bank_sel <= 1'b1;
        end else begin
            if (load_en && !start)
                bank_sel <= 1'b1;

            if (start) begin
                done     <= 1'b0;
                bank_sel <= 1'b1;
            end else if (core_done) begin
                done     <= 1'b1;
                bank_sel <= 1'b1;
            end else if (!start && done) begin
                done <= 1'b0;
            end
        end
    end
endmodule
