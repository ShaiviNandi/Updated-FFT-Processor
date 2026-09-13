// =============================================================================
// tb_activity.v -- toggle-activity comparison, ungated vs operand-isolated
//
// Drives a realistic per-stage precision schedule (the chromosome) into both
// wrappers with identical random data, dumps a VCD, and lets
// count_toggles.py attribute transitions to the FP4 cone and the FP8 cone.
//
// This is the measurement that proves operand isolation does something:
// under the same stimulus the unselected cone must go quiet.
//
// Usage:
//   iverilog -g2012 -DCHROM_FP8_FRAC=<0..8> -o tb_activity.vvp *.v && ./tb_activity.vvp
//   python3 count_toggles.py activity.vcd
// =============================================================================

`timescale 1ns/1ps

module tb_activity;

    // Number of the 8 stages that use FP8 (i.e. popcount of the mult genes).
    // Override at compile time: -DCHROM_FP8_FRAC=3
`ifndef CHROM_FP8_FRAC
  `define CHROM_FP8_FRAC 4
`endif

    localparam integer NSTAGE   = 8;
    localparam integer NFP8     = `CHROM_FP8_FRAC;
    localparam integer PER_STAGE = 128;      // butterflies simulated per stage

    reg         clk = 1'b0;
    reg  [23:0] A, B;
    reg  [15:0] W;
    reg         mult_prec, add_prec;

    wire [15:0] X_ref, Y_ref;  wire ref_f8;
    wire [15:0] X_gat, Y_gat;  wire gat_f8;

    butterfly_wrapper u_ref (
        .A(A), .B(B), .W(W), .mult_prec(mult_prec), .add_prec(add_prec),
        .X(X_ref), .Y(Y_ref), .output_is_fp8(ref_f8)
    );

    butterfly_wrapper_gated #(.PIPELINE_OPERANDS(0)) u_gat (
        .clk(clk),
        .A(A), .B(B), .W(W), .mult_prec(mult_prec), .add_prec(add_prec),
        .X(X_gat), .Y(Y_gat), .output_is_fp8(gat_f8)
    );

    always #5 clk = ~clk;

    reg [63:0] lfsr = 64'h0123456789ABCDEF;
    function [63:0] nxt; input [63:0] s;
        begin nxt = s * 64'd6364136223846793005 + 64'd1442695040888963407; end
    endfunction

    integer s, n;
    initial begin
        $dumpfile("activity.vcd");
        // depth 0 = full hierarchy, so every internal net of both cones is captured
        $dumpvars(0, tb_activity);

        for (s = 0; s < NSTAGE; s = s + 1) begin
            // stages [0 .. NSTAGE-NFP8-1] are FP4, the rest FP8 (late stages
            // need the range, matching the chromosomes NSGA-II actually picks)
            mult_prec = (s >= (NSTAGE - NFP8));
            add_prec  = (s >= (NSTAGE - NFP8));
            for (n = 0; n < PER_STAGE; n = n + 1) begin
                @(posedge clk);
                lfsr = nxt(lfsr); A <= lfsr[55:32];
                lfsr = nxt(lfsr); B <= lfsr[55:32];
                lfsr = nxt(lfsr); W <= lfsr[47:32];
            end
        end
        @(posedge clk); @(posedge clk);
        $display("activity dump complete: %0d stages, %0d FP8 stages, %0d vectors",
                 NSTAGE, NFP8, NSTAGE*PER_STAGE);
        $finish;
    end

endmodule
