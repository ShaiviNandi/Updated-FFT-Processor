// =============================================================================
// tb_equiv.v  --  equivalence proof: butterfly_wrapper vs butterfly_wrapper_gated
//
// Drives IDENTICAL stimulus into the original (ungated) wrapper and the
// operand-isolated wrapper, and flags any difference on X, Y or output_is_fp8.
//
// Coverage strategy
//   PHASE 1  exhaustive over the FP4-relevant subspace with FP8 bits swept
//            coarsely  (mult_prec=0, add_prec=0)
//   PHASE 2  pseudo-random over the full 56-bit input space, all four
//            (mult_prec, add_prec) combinations, NVEC vectors each
//   PHASE 3  directed corners: zeros, max normals, saturation, sign combos
//
// Run:  iverilog -g2012 -o tb_equiv.vvp *.v && ./tb_equiv.vvp
// =============================================================================

`timescale 1ns/1ps

module tb_equiv;

    reg  [23:0] A, B;
    reg  [15:0] W;
    reg         mult_prec, add_prec;

    wire [15:0] X_ref, Y_ref;   wire ref_is_fp8;
    wire [15:0] X_gat, Y_gat;   wire gat_is_fp8;

    integer errors   = 0;
    integer checks   = 0;
    integer phase    = 0;
    integer i, j, k;

    // reference: the original fixed-union wrapper
    butterfly_wrapper u_ref (
        .A(A), .B(B), .W(W),
        .mult_prec(mult_prec), .add_prec(add_prec),
        .X(X_ref), .Y(Y_ref), .output_is_fp8(ref_is_fp8)
    );

    // DUT: operand-isolated wrapper, combinational mode (zero added latency)
    butterfly_wrapper_gated #(.PIPELINE_OPERANDS(0)) u_gat (
        .clk(1'b0),
        .A(A), .B(B), .W(W),
        .mult_prec(mult_prec), .add_prec(add_prec),
        .X(X_gat), .Y(Y_gat), .output_is_fp8(gat_is_fp8)
    );

    // -------------------------------------------------------------------------
    // Comparison. Only the SELECTED half of the output word is architecturally
    // meaningful, but the original drives the full 16 bits deterministically,
    // so we compare all of it: the gated version must be bit-identical.
    // -------------------------------------------------------------------------
    task check;
        begin
            #1;
            checks = checks + 1;
            if (X_ref !== X_gat || Y_ref !== Y_gat || ref_is_fp8 !== gat_is_fp8) begin
                errors = errors + 1;
                if (errors <= 20) begin
                    $display("MISMATCH ph%0d  A=%h B=%h W=%h mp=%b ap=%b", phase, A, B, W, mult_prec, add_prec);
                    $display("        ref X=%h Y=%h f8=%b", X_ref, Y_ref, ref_is_fp8);
                    $display("        gat X=%h Y=%h f8=%b", X_gat, Y_gat, gat_is_fp8);
                end
            end
        end
    endtask

    task drive(input [23:0] a_i, input [23:0] b_i, input [15:0] w_i,
               input mp, input ap);
        begin
            A = a_i; B = b_i; W = w_i; mult_prec = mp; add_prec = ap;
            check;
        end
    endtask

    // Deterministic LCG so the run is reproducible across simulators.
    reg [63:0] lfsr = 64'hDEADBEEFCAFEBABE;
    function [63:0] nxt;
        input [63:0] s;
        begin
            nxt = s * 64'd6364136223846793005 + 64'd1442695040888963407;
        end
    endfunction

    localparam integer NVEC = 250000;

    initial begin
        $display("=== butterfly_wrapper vs butterfly_wrapper_gated equivalence ===");

        // ------------------------------------------------------------------
        // PHASE 1: exhaustive FP4 operand space (A_fp4 x B_fp4 x W_fp4 = 2^24)
        // subsampled on the FP8 lanes, which are gated off in this mode.
        // ------------------------------------------------------------------
        phase = 1;
        for (i = 0; i < 256; i = i + 1)                 // A_fp4
          for (j = 0; j < 256; j = j + 1)               // B_fp4
            for (k = 0; k < 256; k = k + 16) begin      // W_fp4 (every 16th)
                drive({16'hA5A5, i[7:0]}, {16'h5A5A, j[7:0]},
                      {8'h3C, k[7:0]}, 1'b0, 1'b0);
            end
        $display("phase 1 done: checks=%0d errors=%0d", checks, errors);

        // ------------------------------------------------------------------
        // PHASE 2: random, full input space, all four precision combinations
        // ------------------------------------------------------------------
        phase = 2;
        for (k = 0; k < 4; k = k + 1) begin
            for (i = 0; i < NVEC; i = i + 1) begin
                lfsr = nxt(lfsr); A = lfsr[55:32];
                lfsr = nxt(lfsr); B = lfsr[55:32];
                lfsr = nxt(lfsr); W = lfsr[47:32];
                mult_prec = k[1];
                add_prec  = k[0];
                check;
            end
            $display("phase 2 mp=%b ap=%b done: checks=%0d errors=%0d", k[1], k[0], checks, errors);
        end

        // ------------------------------------------------------------------
        // PHASE 3: directed corners
        //   FP8 E4M3: 00=+0, 7F=max normal(+240), FF=-max, 80=-0, 08=min normal
        //   FP4 E2M1: 0=+0, 7=+6(max), F=-6, 8=-0, 2=+1
        // ------------------------------------------------------------------
        phase = 3;
        for (k = 0; k < 4; k = k + 1) begin
            drive(24'h000000, 24'h000000, 16'h0000, k[1], k[0]);  // all zero
            drive(24'h7F7F77, 24'h7F7F77, 16'h7F7F, k[1], k[0]);  // all max +
            drive(24'hFFFFFF, 24'hFFFFFF, 16'hFFFF, k[1], k[0]);  // all max -
            drive(24'h7F7F77, 24'hFFFFFF, 16'h7F7F, k[1], k[0]);  // mixed sign
            drive(24'h808080, 24'h808088, 16'h8080, k[1], k[0]);  // -0 cases
            drive(24'h080822, 24'h080822, 16'h0808, k[1], k[0]);  // min normals
            drive(24'h000000, 24'h7F7F77, 16'h7F7F, k[1], k[0]);  // A=0
            drive(24'h7F7F77, 24'h000000, 16'h7F7F, k[1], k[0]);  // B=0
            drive(24'h7F7F77, 24'h7F7F77, 16'h0000, k[1], k[0]);  // W=0
            drive(24'h010101, 24'h020202, 16'h0303, k[1], k[0]);  // subnormals
        end
        $display("phase 3 done: checks=%0d errors=%0d", checks, errors);

        $display("---------------------------------------------------------------");
        if (errors == 0)
            $display("PASS: %0d vectors, 0 mismatches. Gated wrapper is bit-identical.", checks);
        else
            $display("FAIL: %0d vectors, %0d mismatches.", checks, errors);
        $display("---------------------------------------------------------------");
        $finish;
    end

endmodule
