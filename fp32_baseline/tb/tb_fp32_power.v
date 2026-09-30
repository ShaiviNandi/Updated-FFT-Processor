// =============================================================================
// tb_fp32_power.v -- SAIF activity stimulus for the FP32 baseline FFT cores
//
// FP16 counterpart of tb/tb_fft_power.v.  Same contract:
//   * parameterised by defines, since the module name and size change per design
//       -d DUT_TOP=fp32_fft_256_top  -d FFT_N=256  [-d FRAMES=4]
//   * DUT instance is named `uut`, so read_saif -strip_path <tb>/uut matches
//   * deterministic LCG stimulus -- two designs are only comparable on power
//     if they saw identical data
//   * 10 ns clock, matching POWER_CLOCK_NS and the power report's frequency
//   * handshake + all-zero readback warning only; this is NOT a correctness TB
//     (sim/fp32_performance_evaluator.py covers correctness)
//
// -----------------------------------------------------------------------------
// WHY THE EXPONENT IS CLAMPED
// -----------------------------------------------------------------------------
// fp32_mul short-circuits on `a_zero || b_zero`, exactly as fp8_mul does.  Any
// zero operand skips the whole significand product and normaliser, so random
// 16-bit patterns would silently understate switching activity and report FP16
// as cheaper than it is.
//
// tb_fft_power.v holds FP8 E4M3 exponents in [4, 10].  E4M3 bias is 7, so that
// is a TRUE exponent range of [-3, +3].  FP16 E5M10 bias is 15, so the same
// true range is an exponent field of [12, 18] -- used here.  Matching the true
// exponent range (not the raw field) is what makes the two designs see operands
// of comparable dynamic range, which is the precondition for comparing their
// dynamic power at all.
//
// Mantissas are fully random over all 23 bits; exponents are normal-only
// (never 0, never 31), so no operand is zero, subnormal, Inf or NaN.
// =============================================================================
`timescale 1ns/1ps

`ifndef DUT_TOP
  `define DUT_TOP fp32_fft_256_top
`endif
`ifndef FFT_N
  `define FFT_N 256
`endif
`ifndef FRAMES
  `define FRAMES 4
`endif

module tb_fp32_power;

    localparam N      = `FFT_N;
    localparam FRAMES = `FRAMES;
    localparam AW     = (N <= 2) ? 1 : $clog2(N);

    // 10 ns period -- must match POWER_CLOCK_NS in globalVariablesMixedFFT.py
    localparam real CLK_NS = 10.0;

    reg clk = 1'b0;
    reg rst = 1'b0;
    reg start = 1'b0;
    wire done;

    reg          load_en = 1'b0;
    reg [AW-1:0] load_addr = {AW{1'b0}};
    reg [63:0]   load_data = 64'h0;

    reg          unload_en = 1'b0;
    reg [AW-1:0] unload_addr = {AW{1'b0}};
    wire [63:0]  unload_data;

    // SAIF gating flag.  The current funcsim flow settles past reset and then
    // logs the whole run, so this is exported for compatibility / debug rather
    // than polled.  Frame 0 is included: identical stimulus for every design
    // cannot bias one against another.
    reg saif_window = 1'b0;

    always #(CLK_NS / 2.0) clk = ~clk;

    `DUT_TOP uut (
        .clk         (clk),
        .rst         (rst),
        .start       (start),
        .done        (done),
        .load_en     (load_en),
        .load_addr   (load_addr),
        .load_data   (load_data),
        .unload_en   (unload_en),
        .unload_addr (unload_addr),
        .unload_data (unload_data)
    );

    // -------------------------------------------------------------------------
    // Deterministic 32-bit LCG (glibc constants).  Fixed seed: reproducible
    // across designs, tools and runs.
    // -------------------------------------------------------------------------
    reg [31:0] lcg_state;

    function [31:0] lcg_next;
        input [31:0] s;
        begin
            lcg_next = (s * 32'd1103515245 + 32'd12345);
        end
    endfunction

    // One normal, non-zero FP32 with true exponent in [-3, +3]
    function [31:0] fp32_sample;
        input [31:0] s;
        reg          sgn;
        reg [7:0]    exp_f;
        reg [22:0]   mant;
        begin
            sgn         = s[31];
            // bits [30:28] -> 0..7, folded to 0..6 so exp field is 124..130,
            // i.e. true exponent [-3, +3] with bias 127 -- the same true range
            // tb_fft_power.v gives FP8 E4M3 via its [4, 10] field (bias 7).
            exp_f       = 8'd124 + ((s[30:28] == 3'd7) ? 8'd3 : {5'b0, s[30:28]});
            mant        = s[27:5] ^ {s[4:0], s[22:5]};
            fp32_sample = {sgn, exp_f, mant};
        end
    endfunction

    integer frame, i, cycles, nonzero_reads;
    reg [31:0] s_re, s_im;
    reg [63:0] rd;

    // Guard: a design that never asserts done must not hang the sweep
    localparam TIMEOUT_CYCLES = 400000;

    initial begin
        lcg_state     = 32'h1234_5678;
        nonzero_reads = 0;

        // ---- reset ----
        rst = 1'b0;
        repeat (6) @(posedge clk);
        rst = 1'b1;

        // Short settle past reset before any activity is logged
        #120;
        repeat (2) @(posedge clk);
        saif_window = 1'b1;

        for (frame = 0; frame < FRAMES; frame = frame + 1) begin

            // ---- load N complex FP16 samples ----
            for (i = 0; i < N; i = i + 1) begin
                s_re      = lcg_next(lcg_state);
                s_im      = lcg_next(s_re);
                lcg_state = s_im;

                @(negedge clk);
                load_en   = 1'b1;
                load_addr = i[AW-1:0];
                load_data = {fp32_sample(s_re), fp32_sample(s_im)};
            end
            @(negedge clk);
            load_en   = 1'b0;
            load_data = 64'h0;
            repeat (4) @(posedge clk);

            // ---- run one transform ----
            @(negedge clk); start = 1'b1;
            @(negedge clk); start = 1'b0;

            cycles = 0;
            while (!done && cycles < TIMEOUT_CYCLES) begin
                @(posedge clk);
                cycles = cycles + 1;
            end

            if (!done) begin
                $display("TB_POWER: ERROR frame %0d TIMEOUT after %0d cycles",
                         frame, cycles);
                $finish;
            end
            $display("TB_POWER: frame %0d complete in %0d cycles", frame, cycles);

            repeat (4) @(posedge clk);

            // ---- unload, so the readback path switches too ----
            unload_en = 1'b1;
            for (i = 0; i < N; i = i + 1) begin
                @(negedge clk);
                unload_addr = i[AW-1:0];
                @(negedge clk);
                @(negedge clk);
                rd = unload_data;
                if (rd !== 64'h0)
                    nonzero_reads = nonzero_reads + 1;
            end
            @(negedge clk);
            unload_en = 1'b0;

            repeat (4) @(posedge clk);
        end

        saif_window = 1'b0;
        repeat (4) @(posedge clk);

        if (nonzero_reads == 0)
            $display("TB_POWER: WARNING all readbacks were zero -- datapath may be idle; SAIF would understate activity");
        else
            $display("TB_POWER: %0d of %0d readbacks non-zero across %0d frames",
                     nonzero_reads, N * FRAMES, FRAMES);

        $display("TB_POWER: done N=%0d frames=%0d", N, FRAMES);
        $finish;
    end

endmodule
